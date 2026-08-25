import os
import io
import discord
from discord.ext import commands
from discord.ui import View, Select, Button

# --- RÉCUPÉRATION DES SECRETS DE L'HÉBERGEUR ---
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", 0))

# Configuration des salons fixes et capacités
PRESET_ROOMS_CONFIG = {
    "point-d-eau": 3,
    "foret": 3,
    "plage": 4,
    "riviere": 5
}

rooms_data = {}
dashboard_message = None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def archive_and_purge(channel, guild, reason_text):
    admin_channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
    messages = [msg async for msg in channel.history(limit=200, oldest_first=True)]
    user_messages = [msg for msg in messages if not msg.author.bot]
    
    if user_messages and admin_channel:
        log_text = f"=== ARCHIVE : {channel.name} ===\nÉvénement : {reason_text}\n\n"
        for msg in user_messages:
            log_text += f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author.name} : {msg.content}\n"
        
        file = discord.File(
            fp=io.BytesIO(log_text.encode('utf-8')), 
            filename=f"log_{channel.name}.txt"
        )
        await admin_channel.send(content=f"📁 **Archive de {channel.mention}** (`{reason_text}`)", file=file)
    
    await channel.purge(limit=200)


async def send_room_control_panel(channel, guild):
    room_data = rooms_data.get(channel.id)
    if not room_data:
        return
        
    members_mentions = [guild.get_member(m_id).mention for m_id in room_data["members"] if guild.get_member(m_id)]
    occupants_str = ", ".join(members_mentions) if members_mentions else "Personne"

    embed = discord.Embed(
        title=f"📍 Lieu : {room_data['name'].capitalize()}",
        description=(
            f"**Présents :** {occupants_str}\n"
            f"**Capacité :** {len(room_data['members'])}/{room_data['capacity']}\n\n"
            "💬 Messages archivés et purgés à chaque mouvement.\n"
            "Pour partir : cliquez ci-dessous ou tapez `!quitter`."
        ),
        color=discord.Color.dark_green()
    )
    await channel.send(embed=embed, view=InsideRoomView())


def generate_dashboard_embed(guild):
    embed = discord.Embed(
        title="🗺️ Carte des Lieux de Rencontre", 
        description="Choisissez un lieu dans le menu ci-dessous.\n⚠️ *Tout s'efface quand quelqu'un entre ou sort !*", 
        color=discord.Color.teal()
    )
    for ch_id, data in rooms_data.items():
        channel = guild.get_channel(ch_id)
        if channel:
            members_mentions = [guild.get_member(m_id).mention for m_id in data["members"] if guild.get_member(m_id)]
            occupants_str = "\n".join(members_mentions) if members_mentions else "*Personne sur place*"
            status_icon = "🔴" if len(data["members"]) >= data["capacity"] else "🟢"
            embed.add_field(
                name=f"{status_icon} {data['name'].capitalize()} ({len(data['members'])}/{data['capacity']})", 
                value=occupants_str, 
                inline=True
            )
    return embed


async def refresh_dashboard(guild):
    global dashboard_message
    if dashboard_message:
        try:
            await dashboard_message.edit(embed=generate_dashboard_embed(guild), view=DashboardView(guild))
        except Exception as e:
            print(f"Erreur refresh dashboard : {e}")


async def user_leaves_room(user, guild):
    current_ch_id = None
    for ch_id, data in rooms_data.items():
        if user.id in data["members"]:
            current_ch_id = ch_id
            break

    if not current_ch_id:
        return False, "Vous n'êtes dans aucun lieu."

    channel = guild.get_channel(current_ch_id)
    room = rooms_data[current_ch_id]

    if channel:
        await archive_and_purge(channel, guild, f"{user.name} a QUITTÉ {room['name']}")
        await channel.set_permissions(user, overwrite=None)

    room["members"].remove(user.id)

    if channel and len(room["members"]) > 0:
        await send_room_control_panel(channel, guild)

    await refresh_dashboard(guild)
    return True, f"Vous avez quitté {room['name']}."


class InsideRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🚪 Quitter cet endroit", style=discord.ButtonStyle.danger, custom_id="btn_leave_inside_room")
    async def leave_inside_callback(self, interaction: discord.Interaction, button: Button):
        success, msg = await user_leaves_room(interaction.user, interaction.guild)
        await interaction.response.send_message("✅ Vous êtes sorti du lieu." if success else f"ℹ️ {msg}", ephemeral=True)


class DashboardView(View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild
        
        options = []
        for ch_id, data in rooms_data.items():
            nb = len(data["members"])
            cap = data["capacity"]
            options.append(discord.SelectOption(
                label=f"{data['name'].capitalize()} ({nb}/{cap})", 
                value=str(ch_id), 
                description="Complet !" if nb >= cap else f"Rejoindre ({cap - nb} place(s))",
                emoji="🔴" if nb >= cap else "🟢"
            ))

        if options:
            select = Select(placeholder="📍 Choisir un lieu...", options=options, custom_id="select_location_hub")
            select.callback = self.join_room_callback
            self.add_item(select)

    async def join_room_callback(self, interaction: discord.Interaction):
        user = interaction.user
        target_channel_id = int(interaction.data["values"][0])
        
        if target_channel_id not in rooms_data:
            await interaction.response.send_message("❌ Salon introuvable.", ephemeral=True)
            return

        room = rooms_data[target_channel_id]
        channel = self.guild.get_channel(target_channel_id)

        if user.id in room["members"]:
            await interaction.response.send_message("ℹ️ Vous êtes déjà ici.", ephemeral=True)
            return

        for data in rooms_data.values():
            if user.id in data["members"]:
                await interaction.response.send_message("❌ Quittez votre salon actuel d'abord !", ephemeral=True)
                return

        if len(room["members"]) >= room["capacity"]:
            await interaction.response.send_message("⛔ Ce lieu est plein.", ephemeral=True)
            return

        await archive_and_purge(channel, self.guild, f"{user.name} a REJOINT {room['name']}")
        room["members"].append(user.id)
        await channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)

        await interaction.response.send_message(f"✅ Direction **{room['name'].capitalize()}** ! {channel.mention}", ephemeral=True)
        await send_room_control_panel(channel, self.guild)
        await refresh_dashboard(self.guild)


@bot.command()
async def quitter(ctx):
    success, msg = await user_leaves_room(ctx.author, ctx.guild)
    if not success:
        await ctx.send(f"ℹ️ {msg}", delete_after=5)


@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    global dashboard_message, rooms_data
    await ctx.message.delete()
    
    for name, capacity in PRESET_ROOMS_CONFIG.items():
        channel = discord.utils.get(ctx.guild.text_channels, name=name)
        if not channel:
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            channel = await ctx.guild.create_text_channel(name=name, overwrites=overwrites)
            
        rooms_data[channel.id] = {"name": name, "capacity": capacity, "members": []}

    embed = generate_dashboard_embed(ctx.guild)
    view = DashboardView(ctx.guild)
    dashboard_message = await ctx.send(embed=embed, view=view)


@bot.event
async def on_ready():
    print(f"🤖 Connecté en tant que : {bot.user}")

bot.run(TOKEN)
