import os
import io
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Button

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", 0))

# --- IDS DES CATÉGORIES ---
CATEGORY_ROUGE_ID = 1541039212227465266  # Catégorie Sangaré
CATEGORY_JAUNE_ID = 1541039252064833547  # Catégorie Muntari

TEAMS_CONFIG = {
    "Sangaré": {
        "color": discord.Color.red(),
        "category_id": CATEGORY_ROUGE_ID,
        "rooms": {
            "point-d-eau-rouge": 3,
            "foret-rouge": 3,
            "plage-rouge": 4,
            "riviere-rouge": 5
        }
    },
    "Muntari": {
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

rooms_data = {}
dashboard_messages = {}

class KohLantaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 1. Enregistre le bouton de sortie persistant
        self.add_view(InsideRoomView())

    async def on_ready(self):
        # 2. Reconstruit les états des pièces à partir de Discord
        await restore_state_from_discord(self)
        
        # 3. Synchronise les Slash Commands avec Discord
        try:
            synced = await self.tree.sync()
            print(f"✅ {len(synced)} commandes slash synchronisées.")
        except Exception as e:
            print(f"❌ Erreur lors de la synchronisation des commandes : {e}")

        # 4. Enregistre les Dashboards maintenant que rooms_data est rempli
        for team_name in TEAMS_CONFIG.keys():
            self.add_view(DashboardView(team_name))

        print(f"🤖 Bot connecté et prêt : {self.user}")

bot = KohLantaBot()


# ==========================================
# 🛠️ GESTION DU RECOUVREMENT / FAIL-SAFE
# ==========================================

async def restore_state_from_discord(bot_instance):
    """Scanne les catégories Discord pour reconstruire rooms_data."""
    global rooms_data
    rooms_data.clear()

    for guild in bot_instance.guilds:
        for team_name, config in TEAMS_CONFIG.items():
            category = guild.get_channel(config["category_id"])
            if not category:
                continue

            for room_name, capacity in config["rooms"].items():
                channel = discord.utils.get(category.text_channels, name=room_name)
                if not channel:
                    continue

                # Récupère les membres avec accès explicite
                occupants = []
                for target, overwrite in channel.overwrites.items():
                    if isinstance(target, discord.Member) and not target.bot:
                        if overwrite.read_messages is True:
                            occupants.append(target.id)

                rooms_data[channel.id] = {
                    "name": room_name,
                    "team": team_name,
                    "capacity": capacity,
                    "members": occupants
                }

    print(f"🔄 [Fail-Safe] État restauré : {len(rooms_data)} salons indexés.")


# ==========================================
# 📁 LOGS & CONTRÔLE DES SALONS
# ==========================================

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
            "Pour partir : cliquez sur le bouton ci-dessous ou tapez `/quitter`."
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
                view=DashboardView(team_name)
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

    # Fail-safe supplémentaire : inspection directe des permissions
    if not current_ch_id:
        for ch_id, data in rooms_data.items():
            ch = guild.get_channel(ch_id)
            if ch and ch.overwrites_for(user).read_messages is True:
                current_ch_id = ch_id
                break

    if not current_ch_id:
        return False, "Vous n'êtes dans aucun lieu."

    channel = guild.get_channel(current_ch_id)
    room = rooms_data[current_ch_id]

    if channel:
        await archive_and_purge(channel, guild, f"{user.name} a QUITTÉ {room['name']}")
        await channel.set_permissions(user, overwrite=None)

    if user.id in room["members"]:
        room["members"].remove(user.id)

    if channel and len(room["members"]) > 0:
        await send_room_control_panel(channel, guild)

    await refresh_dashboard_team(guild, room["team"])
    return True, f"Vous avez quitté {room['name']}."


# ==========================================
# 🎛️ VUES ET INTERFACES (PERSISTANTES)
# ==========================================

class InsideRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🚪 Quitter cet endroit", style=discord.ButtonStyle.danger, custom_id="btn_leave_inside_persistent")
    async def leave_inside_callback(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        success, msg = await user_leaves_room(interaction.user, interaction.guild)
        await interaction.followup.send("✅ Vous êtes sorti du lieu." if success else f"ℹ️ {msg}", ephemeral=True)


class DashboardView(View):
    def __init__(self, team_name):
        super().__init__(timeout=None)
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

        if not options:
            options.append(discord.SelectOption(
                label="Aucun salon configuré",
                value="none",
                description="Lancez /setup d'abord"
            ))

        select = Select(
            placeholder=f"📍 Lieux disponibles ({team_name})...",
            options=options,
            custom_id=f"select_team_{team_name.lower()}"
        )
        select.callback = self.join_room_callback
        self.add_item(select)

    async def join_room_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.data["values"][0] == "none":
            await interaction.followup.send("❌ Aucun lieu disponible.", ephemeral=True)
            return

        user = interaction.user
        guild = interaction.guild
        target_channel_id = int(interaction.data["values"][0])

        if target_channel_id not in rooms_data:
            await interaction.followup.send("❌ Salon introuvable.", ephemeral=True)
            return

        target_room = rooms_data[target_channel_id]
        target_channel = guild.get_channel(target_channel_id)

        # Vérification du rôle d'équipe
        user_roles_names = [role.name for role in user.roles]
        if target_room["team"] not in user_roles_names:
            await interaction.followup.send(f"⛔ Vous devez avoir le rôle **{target_room['team']}** pour utiliser ceci !", ephemeral=True)
            return

        if user.id in target_room["members"]:
            await interaction.followup.send("ℹ️ Vous êtes déjà dans ce lieu.", ephemeral=True)
            return

        # Sortir de l'ancien salon si nécessaire
        for ch_id, data in rooms_data.items():
            if user.id in data["members"]:
                await user_leaves_room(user, guild)
                break

        if len(target_room["members"]) >= target_room["capacity"]:
            await interaction.followup.send("⛔ Ce lieu a atteint sa capacité maximale.", ephemeral=True)
            return

        await archive_and_purge(target_channel, guild, f"{user.name} a REJOINT {target_room['name']}")

        target_room["members"].append(user.id)
        await target_channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)

        clean_name = target_room['name'].replace('-rouge', '').replace('-jaune', '').capitalize()
        await interaction.followup.send(f"✅ Direction **{clean_name}** ! {target_channel.mention}", ephemeral=True)
        await send_room_control_panel(target_channel, guild)
        await refresh_dashboard_team(guild, target_room["team"])


# ==========================================
# ⚡ SLASH COMMANDS (/)
# ==========================================

async def setup_team_helper(interaction: discord.Interaction, team_name: str):
    """Fonction commune pour générer le tableau de bord et les salons."""
    global dashboard_messages, rooms_data
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    config = TEAMS_CONFIG[team_name]

    category = guild.get_channel(config["category_id"])
    if not category:
        await interaction.followup.send(f"❌ Erreur : Impossible de trouver la catégorie pour {team_name}. Vérifiez l'ID !", ephemeral=True)
        return

    role = discord.utils.get(guild.roles, name=team_name)
    if not role:
        role = await guild.create_role(name=team_name, color=config["color"])

    for channel_name, capacity in config["rooms"].items():
        channel = discord.utils.get(category.text_channels, name=channel_name)
        if not channel:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        rooms_data[channel.id] = {
            "name": channel_name,
            "team": team_name,
            "capacity": capacity,
            "members": []
        }

    embed = generate_dashboard_embed(guild, team_name)
    view = DashboardView(team_name)
    msg = await interaction.channel.send(embed=embed, view=view)
    dashboard_messages[team_name] = msg
    await interaction.followup.send(f"✅ Tableau de bord pour l'équipe **{team_name}** créé avec succès !", ephemeral=True)


@bot.tree.command(name="setup_sangare", description="Crée le tableau de bord pour l'équipe Sangaré")
@app_commands.default_permissions(administrator=True)
async def slash_setup_sangare(interaction: discord.Interaction):
    await setup_team_helper(interaction, "Sangaré")


@bot.tree.command(name="setup_muntari", description="Crée le tableau de bord pour l'équipe Muntari")
@app_commands.default_permissions(administrator=True)
async def slash_setup_muntari(interaction: discord.Interaction):
    await setup_team_helper(interaction, "Muntari")


@bot.tree.command(name="quitter", description="Quitter votre salon secret actuel")
async def slash_quitter(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    success, msg = await user_leaves_room(interaction.user, interaction.guild)
    await interaction.followup.send("✅ Vous êtes sorti du lieu." if success else f"ℹ️ {msg}", ephemeral=True)


@bot.tree.command(name="synchro", description="Resynchronise manuellement les salons et les tableaux de bord")
@app_commands.default_permissions(administrator=True)
async def slash_synchro(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await restore_state_from_discord(bot)
    for team_name in TEAMS_CONFIG.keys():
        await refresh_dashboard_team(interaction.guild, team_name)
    await interaction.followup.send("✅ Données et dashboards resynchronisés avec succès !", ephemeral=True)


@bot.tree.command(name="expulser", description="Expulse un joueur d'un lieu secret")
@app_commands.describe(membre="Le joueur à expulser du salon")
@app_commands.default_permissions(manage_messages=True)
async def slash_expulser(interaction: discord.Interaction, membre: discord.Member):
    await interaction.response.defer(ephemeral=True)

    found_channel = None
    found_room_name = None

    for ch_id, data in rooms_data.items():
        if membre.id in data["members"]:
            found_channel = interaction.guild.get_channel(ch_id)
            found_room_name = data["name"]
            break

    if not found_channel:
        for team_name, config in TEAMS_CONFIG.items():
            category = interaction.guild.get_channel(config["category_id"])
            if category:
                for ch in category.text_channels:
                    overwrites = ch.overwrites_for(membre)
                    if overwrites.read_messages is True:
                        found_channel = ch
                        found_room_name = ch.name
                        break
            if found_channel:
                break

    if not found_channel:
        await interaction.followup.send(f"❌ {membre.mention} n'a accès à aucun salon secret actuellement.", ephemeral=True)
        return

    clean_name = found_room_name.replace("-rouge", "").replace("-jaune", "").capitalize()
    await archive_and_purge(found_channel, interaction.guild, f"{membre.name} a été EXPULSÉ de {clean_name}")
    await found_channel.set_permissions(membre, overwrite=None)

    if found_channel.id in rooms_data:
        if membre.id in rooms_data[found_channel.id]["members"]:
            rooms_data[found_channel.id]["members"].remove(membre.id)

        if len(rooms_data[found_channel.id]["members"]) > 0:
            await send_room_control_panel(found_channel, interaction.guild)

        await refresh_dashboard_team(interaction.guild, rooms_data[found_channel.id]["team"])

    await interaction.followup.send(f"👟 **{membre.display_name}** a été expulsé(e) du lieu **{clean_name}**.", ephemeral=True)

    try:
        await membre.send(f"⏳ Vous avez été retiré(e) du salon **{clean_name}** par l'arbitre.")
    except discord.Forbidden:
        pass


bot.run(TOKEN)
