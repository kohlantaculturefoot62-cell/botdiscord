import os
import io
import discord
from discord.ext import commands
from discord.ui import View, Select, Button

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", 0))

TEAMS_CONFIG = {
    "Rouge": {
        "color": discord.Color.red(),
        "rooms": {"point-d-eau-rouge": 3, "foret-rouge": 3, "plage-rouge": 4, "riviere-rouge": 5}
    },
    "Jaune": {
        "color": discord.Color.yellow(),
        "rooms": {"point-d-eau-jaune": 3, "foret-jaune": 3, "plage-jaune": 4, "riviere-jaune": 5}
    }
}

# {channel_id: {"name": str, "team": str, "capacity": int, "members": [user_ids]}}
rooms_data = {}
dashboard_messages = {}  # {"Équipe Rouge": message, "Équipe Bleue": message}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def archive_and_purge(channel, guild, reason_text):
    """Archive les messages utilisateur vers le salon admin et vide le salon."""
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
    """Envoie l'encadré d'accueil et le bouton de sortie dans le salon secret."""
    room_data = rooms_data.get(channel.id)
    if not room_data:
        return

    members_mentions = [guild.get_member(m_id).mention for m_id in room_data["members"] if guild.get_member(m_id)]
    occupants_str = ", ".join(members_mentions) if members_mentions else "Personne"

    embed = discord.Embed(
        title=f"📍 Lieu : {room_data['name'].replace('-rouge', '').replace('-jaune', '').capitalize()}",
        description=(
            f"**Équipe :** {room_data['team']}\n"
            f"**Présents :** {occupants_str}\n"
            f"**Capacité :** {len(room_data['members'])}/{room_data['capacity']}\n\n"
            "💬 Les messages sont effacés dès qu'un membre entre ou sort.\n"
            "Pour partir : cliquez sur le bouton ci-dessous ou tapez `!quitter`."
        ),
        color=TEAMS_CONFIG[room_data['team']]['color']
    )
    await channel.send(embed=embed, view=InsideRoomView())


def generate_dashboard_embed(guild, team_name):
    """Génère l'embed pour une équipe donnée."""
    team_info = TEAMS_CONFIG[team_name]
    embed = discord.Embed(
        title=f"🗺️ Carte des Lieux — {team_name}",
        description=(
            "Sélectionnez un lieu ci-dessous pour vous y déplacer.\n"
            "Si vous êtes déjà dans une pièce, vous changerez automatiquement d'endroit.\n"
            "⚠️ *L'historique est effacé et envoyé aux admins à chaque mouvement.*"
        ),
        color=team_info["color"]
    )

    for ch_id, data in rooms_data.items():
        if data["team"] == team_name:
            channel = guild.get_channel(ch_id)
            if channel:
                members_mentions = [guild.get_member(m_id).mention for m_id in data["members"] if guild.get_member(m_id)]
                occupants_str = "\n".join(members_mentions) if members_mentions else "*Personne sur place*"
                status_icon = "🔴" if len(data["members"]) >= data["capacity"] else "🟢"
                clean_name = data['name'].replace('-rouge', '').replace('-jaune', '').capitalize()

                embed.add_field(
                    name=f"{status_icon} {clean_name} ({len(data['members'])}/{data['capacity']})",
                    value=occupants_str,
                    inline=True
                )
    return embed


async def refresh_all_dashboards(guild):
    """Met à jour les dashboards des deux équipes."""
    for team_name, message in dashboard_messages.items():
        if message:
            try:
                await message.edit(
                    embed=generate_dashboard_embed(guild, team_name),
                    view=DashboardView(guild, team_name)
                )
            except Exception as e:
                print(f"Erreur actualisation dashboard {team_name} : {e}")


async def user_leaves_room(user, guild):
    """Retire un membre de sa salle actuelle avec purge et archive."""
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

    await refresh_all_dashboards(guild)
    return True, f"Vous avez quitté {room['name']}."


class InsideRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🚪 Quitter cet endroit", style=discord.ButtonStyle.danger, custom_id="btn_leave_inside")
    async def leave_inside_callback(self, interaction: discord.Interaction, button: Button):
        # Réponse différée pour éviter l'erreur 10062
        await interaction.response.defer(ephemeral=True)
        success, msg = await user_leaves_room(interaction.user, interaction.guild)
        await interaction.followup.send("✅ Vous êtes sorti du lieu." if success else f"ℹ️ {msg}", ephemeral=True)


class DashboardView(View):
    def __init__(self, guild, team_name):
        super().__init__(timeout=None)
        self.guild = guild
        self.team_name = team_name

        options = []
        for ch_id, data in rooms_data.items():
            if data["team"] == team_name:
                nb = len(data["members"])
                cap = data["capacity"]
                clean_name = data['name'].replace('-rouge', '').replace('-jaune', '').capitalize()
                
                options.append(discord.SelectOption(
                    label=f"{clean_name} ({nb}/{cap})",
                    value=str(ch_id),
                    description="Complet !" if nb >= cap else f"Rejoindre ({cap - nb} place(s) restante(s))",
                    emoji="🔴" if nb >= cap else "🟢"
                ))

        if options:
            select = Select(
                placeholder=f"📍 Lieux — {team_name}...",
                options=options,
                custom_id=f"select_{team_name.lower().replace(' ', '_')}"
            )
            select.callback = self.join_room_callback
            self.add_item(select)

    async def join_room_callback(self, interaction: discord.Interaction):
        # 1. Différer la réponse immédiatement pour bloquer l'erreur Discord Timeout (10062)
        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        target_channel_id = int(interaction.data["values"][0])

        if target_channel_id not in rooms_data:
            await interaction.followup.send("❌ Ce salon est introuvable.", ephemeral=True)
            return

        target_room = rooms_data[target_channel_id]
        target_channel = self.guild.get_channel(target_channel_id)

        # Vérification du rôle d'équipe
        user_roles_names = [role.name for role in user.roles]
        if target_room["team"] not in user_roles_names:
            await interaction.followup.send(f"⛔ Vous n'appartenez pas à l'**{target_room['team']}** !", ephemeral=True)
            return

        # Si l'utilisateur est déjà dans ce salon précis
        if user.id in target_room["members"]:
            await interaction.followup.send("ℹ️ Vous êtes déjà dans ce lieu.", ephemeral=True)
            return

        # S'il est dans un autre salon : le faire quitter automatiquement avant d'entrer
        for ch_id, data in rooms_data.items():
            if user.id in data["members"]:
                await user_leaves_room(user, self.guild)
                break

        # Vérifier la capacité
        if len(target_room["members"]) >= target_room["capacity"]:
            await interaction.followup.send("⛔ Ce lieu est plein.", ephemeral=True)
            return

        # Purge et archive du nouveau salon
        await archive_and_purge(target_channel, self.guild, f"{user.name} a REJOINT {target_room['name']}")

        # Donner l'accès
        target_room["members"].append(user.id)
        await target_channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)

        clean_name = target_room['name'].replace('-rouge', '').replace('-jaune', '').capitalize()
        await interaction.followup.send(f"✅ Direction **{clean_name}** ! {target_channel.mention}", ephemeral=True)
        await send_room_control_panel(target_channel, self.guild)
        await refresh_all_dashboards(self.guild)


@bot.command()
async def quitter(ctx):
    success, msg = await user_leaves_room(ctx.author, ctx.guild)
    if not success:
        await ctx.send(f"ℹ️ {msg}", delete_after=5)


@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    """Initialise les rôles, catégories, salons par équipe et dashboards."""
    global dashboard_messages, rooms_data
    await ctx.message.delete()
    guild = ctx.guild

    for team_name, config in TEAMS_CONFIG.items():
        # 1. Créer ou récupérer le rôle d'équipe
        role = discord.utils.get(guild.roles, name=team_name)
        if not role:
            role = await guild.create_role(name=team_name, color=config["color"])

        # 2. Créer ou récupérer la catégorie d'équipe (fermée aux autres rôles)
        cat_overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            role: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        category = discord.utils.get(guild.categories, name=team_name.upper())
        if not category:
            category = await guild.create_category(name=team_name.upper(), overwrites=cat_overwrites)

        # 3. Créer les salons de l'équipe dans sa catégorie
        for channel_name, capacity in config["rooms"].items():
            channel = discord.utils.get(guild.text_channels, name=channel_name, category=category)
            if not channel:
                room_overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    role: discord.PermissionOverwrite(read_messages=False),  # Invisible jusqu'à ce qu'ils le rejoignent
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=room_overwrites)

            rooms_data[channel.id] = {
                "name": channel_name,
                "team": team_name,
                "capacity": capacity,
                "members": []
            }

        # 4. Envoyer le Dashboard dédié à l'équipe dans le salon où la commande est tapée
        embed = generate_dashboard_embed(guild, team_name)
        view = DashboardView(guild, team_name)
        msg = await ctx.send(embed=embed, view=view)
        dashboard_messages[team_name] = msg


@bot.event
async def on_ready():
    print(f"🤖 Bot connecté sous le nom de : {bot.user}")

bot.run(TOKEN)
