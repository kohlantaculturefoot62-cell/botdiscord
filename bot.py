import os
import io
import discord
from discord.ext import commands
from discord.ui import View, Select, Button

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", 0))

# --- IDS DES CATÉGORIES (Clic droit sur la catégorie -> Copier l'identifiant) ---
CATEGORY_ROUGE_ID = 1541039212227465266  # Remplacez par l'ID de votre catégorie Rouge
CATEGORY_JAUNE_ID = 1541039252064833547  # Remplacez par l'ID de votre catégorie Jaune

TEAMS_CONFIG = {
    "Rouge": {
        "color": discord.Color.red(),
        "category_id": CATEGORY_ROUGE_ID,
        "rooms": {
            "point-d-eau-rouge": 3,
            "foret-rouge": 3,
            "plage-rouge": 4,
            "riviere-rouge": 5
        }
    },
    "Jaune": {
        "color": discord.Color.gold(),
        "category_id": CATEGORY_JAUNE_ID,
        "rooms": {
            "point-d-eau-jaune": 3,
            "foret-jaune": 3,
            "plage-jaune": 4,
            "riviere-jaune": 5
        }
    }
}

# Structure : {channel_id: {"name": str, "team": str, "capacity": int, "members": [user_ids]}}
rooms_data = {}
dashboard_messages = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def archive_and_purge(channel, guild, reason_text):
    """Archive les messages vers le salon admin et purge."""
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
    """Envoie le message d'accueil et le bouton de sortie dans le salon secret."""
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
            "💬 Les messages sont effacés et archivés dès qu'un membre entre ou sort.\n"
            "Pour partir : cliquez sur le bouton ci-dessous ou tapez `!quitter`."
        ),
        color=TEAMS_CONFIG[room_data['team']]['color']
    )
    await channel.send(embed=embed, view=InsideRoomView())


def generate_dashboard_embed(guild, team_name):
    """Génère l'embed pour l'équipe sélectionnée."""
    team_info = TEAMS_CONFIG[team_name]
    embed = discord.Embed(
        title=f"🗺️ Lieux de Rencontre — Équipe {team_name}",
        description=(
            "Sélectionnez un lieu ci-dessous pour vous y déplacer.\n"
            "Si vous êtes déjà dans un salon, vous changerez automatiquement de pièce.\n"
            "⚠️ *L'historique est purgé et envoyé aux admins à chaque mouvement.*"
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


async def refresh_dashboard_team(guild, team_name):
    """Met à jour le tableau de bord d'une équipe."""
    message = dashboard_messages.get(team_name)
    if message:
        try:
            await message.edit(
                embed=generate_dashboard_embed(guild, team_name),
                view=DashboardView(guild, team_name)
            )
        except Exception as e:
            print(f"Erreur actualisation dashboard {team_name} : {e}")


async def user_leaves_room(user, guild):
    """Fait sortir un membre de son salon actuel."""
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

    await refresh_dashboard_team(guild, room["team"])
    return True, f"Vous avez quitté {room['name']}."


class InsideRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🚪 Quitter cet endroit", style=discord.ButtonStyle.danger, custom_id="btn_leave_inside")
    async def leave_inside_callback(self, interaction: discord.Interaction, button: Button):
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
                placeholder=f"📍 Lieux disponibles ({team_name})...",
                options=options,
                custom_id=f"select_{team_name.lower()}"
            )
            select.callback = self.join_room_callback
            self.add_item(select)

    async def join_room_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        target_channel_id = int(interaction.data["values"][0])

        if target_channel_id not in rooms_data:
            await interaction.followup.send("❌ Salon introuvable.", ephemeral=True)
            return

        target_room = rooms_data[target_channel_id]
        target_channel = self.guild.get_channel(target_channel_id)

        # Vérification du rôle d'équipe
        user_roles_names = [role.name for role in user.roles]
        if target_room["team"] not in user_roles_names:
            await interaction.followup.send(f"⛔ Vous devez avoir le rôle **{target_room['team']}** pour utiliser ceci !", ephemeral=True)
            return

        if user.id in target_room["members"]:
            await interaction.followup.send("ℹ️ Vous êtes déjà dans ce lieu.", ephemeral=True)
            return

        # S'il est dans une autre pièce, le sortir d'abord
        for ch_id, data in rooms_data.items():
            if user.id in data["members"]:
                await user_leaves_room(user, self.guild)
                break

        # Vérifier la capacité
        if len(target_room["members"]) >= target_room["capacity"]:
            await interaction.followup.send("⛔ Ce lieu a atteint sa capacité maximale.", ephemeral=True)
            return

        # Purge et archive du nouveau salon
        await archive_and_purge(target_channel, self.guild, f"{user.name} a REJOINT {target_room['name']}")

        # Donner l'accès
        target_room["members"].append(user.id)
        await target_channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)

        clean_name = target_room['name'].replace('-rouge', '').replace('-jaune', '').capitalize()
        await interaction.followup.send(f"✅ Direction **{clean_name}** ! {target_channel.mention}", ephemeral=True)
        await send_room_control_panel(target_channel, self.guild)
        await refresh_dashboard_team(self.guild, target_room["team"])


async def handle_team_setup(ctx, team_name):
    """Crée les salons dans la catégorie existante et poste le tableau de bord."""
    global dashboard_messages, rooms_data
    await ctx.message.delete()
    guild = ctx.guild
    config = TEAMS_CONFIG[team_name]

    category = guild.get_channel(config["category_id"])
    if not category:
        await ctx.send(f"❌ Erreur : Impossible de trouver la catégorie pour {team_name}. Vérifiez l'ID !", delete_after=10)
        return

    # S'assurer que le rôle existe
    role = discord.utils.get(guild.roles, name=team_name)
    if not role:
        role = await guild.create_role(name=team_name, color=config["color"])

    # Création des salons dans votre catégorie existante
    for channel_name, capacity in config["rooms"].items():
        channel = discord.utils.get(category.text_channels, name=channel_name)
        if not channel:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            channel = await ctx.guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        rooms_data[channel.id] = {
            "name": channel_name,
            "team": team_name,
            "capacity": capacity,
            "members": []
        }

    embed = generate_dashboard_embed(guild, team_name)
    view = DashboardView(guild, team_name)
    msg = await ctx.send(embed=embed, view=view)
    dashboard_messages[team_name] = msg


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_rouge(ctx):
    """À taper dans le salon d'accueil de l'équipe Rouge."""
    await handle_team_setup(ctx, "Rouge")

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_jaune(ctx):
    """À taper dans le salon d'accueil de l'équipe Jaune."""
    await handle_team_setup(ctx, "Jaune")

@bot.command()
async def quitter(ctx):
    """Commande manuelle pour quitter un salon."""
    success, msg = await user_leaves_room(ctx.author, ctx.guild)
    if not success:
        await ctx.send(f"ℹ️ {msg}", delete_after=5)

@bot.event
async def on_ready():
    print(f"🤖 Bot connecté : {bot.user}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def expulser(ctx, member: discord.Member):
    """Permet aux admins/modos d'éjecter un candidat d'un salon : !expulser @Membre"""
    # 1. Vérifier si le membre est bien dans un salon
    user_room_id = None
    for ch_id, data in rooms_data.items():
        if member.id in data["members"]:
            user_room_id = ch_id
            break

    if not user_room_id:
        await ctx.send(f"❌ {member.mention} n'est actuellement dans aucun salon secret.", delete_after=5)
        return

    room_name = rooms_data[user_room_id]["name"].replace("-rouge", "").replace("-jaune", "").capitalize()

    # 2. Sortir le joueur (archive, purge et mise à jour du dashboard)
    success, msg = await user_leaves_room(member, ctx.guild)

    if success:
        await ctx.send(f"👟 **{member.display_name}** a été expulsé(e) du lieu **{room_name}**.", delete_after=8)
        # Optionnel : Envoyer un message privé au joueur expulsé
        try:
            await member.send(f"⏳ Vous avez été retiré(e) du lieu **{room_name}** (temps écoulé / décision de l'arbitre).")
        except discord.Forbidden:
            pass
    else:
        await ctx.send(f"⚠️ Erreur lors de l'expulsion : {msg}", delete_after=5)

    # Nettoyer la commande écrite pour garder le salon propre
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
        
bot.run(TOKEN)
