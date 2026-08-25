import discord
from discord.ext import commands
from discord.ui import View, Select, Button
import io

# --- CONFIGURATION ---
TOKEN = "MTU0MTg5ODAzNjE2MTAyMDA1NQ.G_nY28.K2LUJwNdvnm9SRp1h86hTUjtAv31XELMBLdlLY"
ADMIN_LOG_CHANNEL_ID = 123456789012345678  # ID de ton salon admin
CATEGORY_ID = None  # (Optionnel) ID de la catégorie des salons

# Salons prédéfinis avec leurs limites
PRESET_ROOMS_CONFIG = {
    "point-d-eau": 3,
    "foret": 3,
    "plage": 4,
    "riviere": 5
}

# Structure : {channel_id: {"name": str, "capacity": int, "members": [user_ids]}}
rooms_data = {}
dashboard_message = None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# --- FONCTIONS UTILITAIRES ---

async def archive_and_purge(channel, guild, reason_text):
    """Archive les messages vers le salon admin et vide le salon."""
    admin_channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
    messages = [msg async for msg in channel.history(limit=200, oldest_first=True)]
    
    # Filtrer les messages pour ne garder que ceux des vrais utilisateurs
    user_messages = [msg for msg in messages if not msg.author.bot]
    
    if user_messages and admin_channel:
        log_text = f"=== ARCHIVE : {channel.name} ===\nÉvénement : {reason_text}\n\n"
        for msg in user_messages:
            log_text += f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author.name} : {msg.content}\n"
        
        file = discord.File(
            fp=io.BytesIO(log_text.encode('utf-8')), 
            filename=f"log_{channel.name}_{msg.created_at.strftime('%d%m%Y_%H%M%S')}.txt"
        )
        await admin_channel.send(content=f"📁 **Archive de {channel.mention}** (`{reason_text}`)", file=file)
    
    # Purge tous les messages du salon
    await channel.purge(limit=200)


async def send_room_control_panel(channel, guild):
    """Envoie le message de bienvenue et le bouton de sortie à l'intérieur du salon secret."""
    room_data = rooms_data.get(channel.id)
    if not room_data:
        return
        
    members_mentions = [guild.get_member(m_id).mention for m_id in room_data["members"] if guild.get_member(m_id)]
    occupants_str = ", ".join(members_mentions) if members_mentions else "Personne"

    embed = discord.Embed(
        title=f"📍 Bienvenue à : {room_data['name'].capitalize()}",
        description=(
            f"**Présents actuellement :** {occupants_str}\n"
            f"**Capacité :** {len(room_data['members'])}/{room_data['capacity']}\n\n"
            "💬 Vous pouvez discuter librement. Si quelqu'un entre ou sort, tous les messages seront effacés.\n"
            "Pour partir, cliquez sur le bouton ci-dessous ou tapez `!quitter`."
        ),
        color=discord.Color.dark_green()
    )
    view = InsideRoomView()
    await channel.send(embed=embed, view=view)


def generate_dashboard_embed(guild):
    """Tableau de bord pour le salon d'accueil."""
    embed = discord.Embed(
        title="🗺️ Carte des Lieux de Rencontre", 
        description=(
            "Sélectionnez un lieu dans le menu ci-dessous pour vous y rendre.\n\n"
            "⚠️ **Règle de confidentialité :**\n"
            "Dès qu'un candidat entre ou quitte une zone, la conversation est archivée aux admins et le salon redevient totalement vierge."
        ), 
        color=discord.Color.teal()
    )
    
    for ch_id, data in rooms_data.items():
        channel = guild.get_channel(ch_id)
        if channel:
            members_mentions = [guild.get_member(m_id).mention for m_id in data["members"] if guild.get_member(m_id)]
            occupants_str = "\n".join(members_mentions) if members_mentions else "*Personne sur place*"
            
            is_full = len(data["members"]) >= data["capacity"]
            status_icon = "🔴" if is_full else "🟢"
            
            embed.add_field(
                name=f"{status_icon} {data['name'].capitalize()} ({len(data['members'])}/{data['capacity']})", 
                value=occupants_str, 
                inline=True
            )
            
    return embed


async def refresh_dashboard(guild):
    """Met à jour le message du Dashboard."""
    global dashboard_message
    if dashboard_message:
        try:
            await dashboard_message.edit(embed=generate_dashboard_embed(guild), view=DashboardView(guild))
        except Exception as e:
            print(f"Erreur d'actualisation du dashboard: {e}")


async def user_leaves_room(user, guild):
    """Fonction commune pour faire sortir un utilisateur d'un salon."""
    current_ch_id = None

    for ch_id, data in rooms_data.items():
        if user.id in data["members"]:
            current_ch_id = ch_id
            break

    if not current_ch_id:
        return False, "Vous n'êtes dans aucun lieu."

    channel = guild.get_channel(current_ch_id)
    room = rooms_data[current_ch_id]

    # 1. Archive et purge
    if channel:
        await archive_and_purge(channel, guild, f"{user.name} a QUITTÉ {room['name']}")
        await channel.set_permissions(user, overwrite=None)

    # 2. Retirer de la liste
    room["members"].remove(user.id)

    # 3. Si d'autres personnes restent dans le salon, renvoyer le panneau de contrôle
    if channel and len(room["members"]) > 0:
        await send_room_control_panel(channel, guild)

    # 4. Mettre à jour le lobby
    await refresh_dashboard(guild)
    return True, f"Vous avez quitté {room['name']}."


# --- VUE INTERNE AU SALON (BOUTON DE SORTIE DEDANS) ---

class InsideRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🚪 Quitter cet endroit", style=discord.ButtonStyle.danger, custom_id="btn_leave_inside_room")
    async def leave_inside_callback(self, interaction: discord.Interaction, button: Button):
        success, msg = await user_leaves_room(interaction.user, interaction.guild)
        if success:
            await interaction.response.send_message("✅ Vous avez quitté les lieux.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {msg}", ephemeral=True)


# --- VUE DU DASHBOARD (SALON D'ACCUEIL) ---

class DashboardView(View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild
        
        options = []
        for ch_id, data in rooms_data.items():
            nb = len(data["members"])
            cap = data["capacity"]
            label_txt = f"{data['name'].capitalize()} ({nb}/{cap})"
            desc = "Complet !" if nb >= cap else f"Rejoindre ({cap - nb} place(s) restante(s))"
            
            options.append(discord.SelectOption(
                label=label_txt, 
                value=str(ch_id), 
                description=desc,
                emoji="🔴" if nb >= cap else "🟢"
            ))

        if options:
            select = Select(
                placeholder="📍 Choisir un lieu à explorer...", 
                options=options, 
                custom_id="select_location_hub"
            )
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

        # Vérifications
        if user.id in room["members"]:
            await interaction.response.send_message("ℹ️ Vous êtes déjà dans ce lieu.", ephemeral=True)
            return

        for data in rooms_data.values():
            if user.id in data["members"]:
                await interaction.response.send_message("❌ Vous êtes déjà dans un autre salon. Quittez-le d'abord !", ephemeral=True)
                return

        if len(room["members"]) >= room["capacity"]:
            await interaction.response.send_message("⛔ Ce lieu est plein.", ephemeral=True)
            return

        # 1. Archive et purge du salon avant l'entrée
        await archive_and_purge(channel, self.guild, f"{user.name} a REJOINT {room['name']}")

        # 2. Ajout de l'utilisateur
        room["members"].append(user.id)
        await channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)

        # 3. Répondre et envoyer le panneau de commande à l'intérieur du salon
        await interaction.response.send_message(f"✅ Direction **{room['name'].capitalize()}** ! {channel.mention}", ephemeral=True)
        await send_room_control_panel(channel, self.guild)
        await refresh_dashboard(self.guild)


# --- COMMANDES ---

@bot.command()
async def quitter(ctx):
    """Permet à un candidat de taper !quitter directement dans le salon."""
    success, msg = await user_leaves_room(ctx.author, ctx.guild)
    if success:
        try:
            await ctx.author.send(f"✅ {msg}")
        except:
            pass
    else:
        await ctx.send(f"ℹ️ {msg}", delete_after=5)


@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    """Configure les 4 salons et le dashboard."""
    global dashboard_message, rooms_data
    await ctx.message.delete()
    
    category = ctx.guild.get_channel(CATEGORY_ID) if CATEGORY_ID else None
    
    for name, capacity in PRESET_ROOMS_CONFIG.items():
        channel = discord.utils.get(ctx.guild.text_channels, name=name)
        
        if not channel:
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            channel = await ctx.guild.create_text_channel(name=name, category=category, overwrites=overwrites)
            
        rooms_data[channel.id] = {
            "name": name,
            "capacity": capacity,
            "members": []
        }

    embed = generate_dashboard_embed(ctx.guild)
    view = DashboardView(ctx.guild)
    dashboard_message = await ctx.send(embed=embed, view=view)


@bot.event
async def on_ready():
    print(f"🤖 Bot prêt et connecté : {bot.user}")

bot.run(TOKEN)
