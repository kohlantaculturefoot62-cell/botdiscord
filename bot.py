import os
import io
import json
import random
import asyncio
import datetime
import unicodedata
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Button

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", 0))

# --- IDS DES CATÉGORIES ET RÔLES ---
CATEGORY_ROUGE_ID = 1541039212227465266  # Catégorie Sangaré
CATEGORY_JAUNE_ID = 1541039252064833547  # Catégorie Muntari
CATEGORY_REUNIFIE_ID = 1537440948697432064

CANDIDAT_ROLE_ID = 1535332671499280525    # Rôle @candidats

ELIMINE_ROLE_ID = 1535332789833171087     # Rôle @éliminés (anciens candidats)
SPECTATEUR_ROLE_ID = 1535332731221967059  # Rôle @spectateurs

# Rôle @orga : mets son ID ici, ou dans la variable d'environnement ORGA_ROLE_ID.
# Si l'ID vaut 0, le bot cherche un rôle dont le nom contient "orga" (orga, organisateur, organisation...).
ORGA_ROLE_ID = 1535332585671233676

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
    },
    "Réunification": {
        "color": discord.Color.purple(),
        "category_id": CATEGORY_REUNIFIE_ID,  # Si 0, réutilise la catégorie où la commande /setup est lancée
        "rooms": {
            "plage-reunifiee": 6,
            "foret-reunifiee": 4,
            "point-d-eau-reunifie": 3,
            "riviere-reunifie": 8
        }
    }
}

rooms_data = {}
dashboard_messages = {}

# Catégories (salons secrets) JAMAIS utilisées pour les citations du jeu "Qui a dit ça ?"
QUOTE_EXCLUDED_CATEGORY_IDS = {CATEGORY_ROUGE_ID, CATEGORY_JAUNE_ID, CATEGORY_REUNIFIE_ID}

# Fichier qui mémorise les phrases déjà utilisées (pour ne jamais les répéter)
USED_QUOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "used_quotes.json")


class KohLantaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(InsideRoomView())

    async def on_ready(self):
        await restore_state_from_discord(self)

        try:
            synced = await self.tree.sync()
            print(f"✅ {len(synced)} commandes slash synchronisées.")
        except Exception as e:
            print(f"❌ Erreur synchronisation commandes : {e}")

        for team_name in TEAMS_CONFIG.keys():
            self.add_view(DashboardView(team_name))

        print(f"🤖 Bot connecté et prêt : {self.user}")

bot = KohLantaBot()


# ==========================================
# 🛠️ GESTION DU RECOUVREMENT / FAIL-SAFE
# ==========================================

async def restore_state_from_discord(bot_instance):
    """Scanne uniquement les catégories définies pour reconstruire rooms_data proprement."""
    global rooms_data
    rooms_data.clear()

    for guild in bot_instance.guilds:
        for team_name, config in TEAMS_CONFIG.items():
            cat_id = config["category_id"]
            if not cat_id:
                continue

            category = guild.get_channel(cat_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                continue

            for room_name, capacity in config["rooms"].items():
                channel = discord.utils.get(category.text_channels, name=room_name)
                if not channel:
                    continue

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
        title=f"📍 Lieu : {room_data['name'].replace('-rouge', '').replace('-jaune', '').replace('-reunifiee', '').replace('-reunifie', '').capitalize()}",
        description=(
            f"**Camp/Équipe :** {room_data['team']}\n"
            f"**Présents :** {occupants_str}\n"
            f"**Capacité :** {len(room_data['members'])}/{room_data['capacity']}\n\n"
            "💬 Les messages sont effacés et archivés dès qu'un membre entre ou sort.\n"
            "Pour partir : cliquez sur le bouton ci-dessous ou tapez `/quitter`."
        ),
        color=TEAMS_CONFIG[room_data['team']]['color']
    )
    await channel.send(embed=embed, view=InsideRoomView())


def generate_dashboard_embed(guild, team_name):
    """Génère l'embed pour le camp/l'équipe sélectionnée."""
    team_info = TEAMS_CONFIG[team_name]
    embed = discord.Embed(
        title=f"🗺️ Lieux de Rencontre — {team_name}",
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
                clean_name = data['name'].replace('-rouge', '').replace('-jaune', '').replace('-reunifiee', '').replace('-reunifie', '').capitalize()

                embed.add_field(
                    name=f"{status_icon} {clean_name} ({len(data['members'])}/{data['capacity']})",
                    value=occupants_str,
                    inline=True
                )
    return embed


async def refresh_dashboard_team(guild, team_name):
    """Met à jour le tableau de bord d'une équipe ou du camp réunifié."""
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
                clean_name = data['name'].replace('-rouge', '').replace('-jaune', '').replace('-reunifiee', '').replace('-reunifie', '').capitalize()

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

        # 1. Vérification du rôle @Candidat pour tous
        candidat_role = guild.get_role(CANDIDAT_ROLE_ID)
        if candidat_role and candidat_role not in user.roles:
            await interaction.followup.send("⛔ Seuls les membres ayant le rôle **Candidat** peuvent rejoindre ces lieux.", ephemeral=True)
            return

        # 2. Vérification du rôle d'équipe (sauf pour la réunification où seule l'appartenance au rôle candidat suffit)
        if target_room["team"] != "Réunification":
            user_roles_names = [role.name for role in user.roles]
            if target_room["team"] not in user_roles_names:
                await interaction.followup.send(f"⛔ Vous devez avoir le rôle **{target_room['team']}** pour utiliser ceci !", ephemeral=True)
                return

        if user.id in target_room["members"]:
            await interaction.followup.send("ℹ️ Vous êtes déjà dans ce lieu.", ephemeral=True)
            return

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

        clean_name = target_room['name'].replace('-rouge', '').replace('-jaune', '').replace('-reunifiee', '').replace('-reunifie', '').capitalize()
        await interaction.followup.send(f"✅ Direction **{clean_name}** ! {target_channel.mention}", ephemeral=True)
        await send_room_control_panel(target_channel, guild)
        await refresh_dashboard_team(guild, target_room["team"])


# ==========================================
# ⚡ SLASH COMMANDS (/)
# ==========================================

async def setup_team_helper(interaction: discord.Interaction, team_name: str):
    """Crée ou réutilise les salons sans créer de doublons en mémoire."""
    global dashboard_messages, rooms_data
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    config = TEAMS_CONFIG[team_name]

    # Détermination de la catégorie cible
    cat_id = config["category_id"]
    category = guild.get_channel(cat_id) if cat_id else interaction.channel.category

    if not category or not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(f"❌ Impossible de trouver la catégorie pour {team_name}. Lancez la commande directement dans la catégorie voulue ou configurez un ID valide !", ephemeral=True)
        return

    TEAMS_CONFIG[team_name]["category_id"] = category.id

    # Nettoyage des anciens salons en mémoire pour ce camp
    to_delete = [ch_id for ch_id, d in rooms_data.items() if d["team"] == team_name]
    for ch_id in to_delete:
        del rooms_data[ch_id]

    candidat_role = guild.get_role(CANDIDAT_ROLE_ID)
    team_role = discord.utils.get(guild.roles, name=team_name)

    for channel_name, capacity in config["rooms"].items():
        channel = discord.utils.get(category.text_channels, name=channel_name)
        if not channel:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            if candidat_role:
                overwrites[candidat_role] = discord.PermissionOverwrite(read_messages=False)
            if team_role:
                overwrites[team_role] = discord.PermissionOverwrite(read_messages=False)

            channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        occupants = [
            target.id for target, ow in channel.overwrites.items()
            if isinstance(target, discord.Member) and not target.bot and ow.read_messages is True
        ]

        rooms_data[channel.id] = {
            "name": channel_name,
            "team": team_name,
            "capacity": capacity,
            "members": occupants
        }

    embed = generate_dashboard_embed(guild, team_name)
    view = DashboardView(team_name)
    msg = await interaction.channel.send(embed=embed, view=view)
    dashboard_messages[team_name] = msg
    await interaction.followup.send(f"✅ Tableau de bord pour **{team_name}** initialisé !", ephemeral=True)


@bot.tree.command(name="setup_sangare", description="Crée le tableau de bord pour l'équipe Sangaré")
@app_commands.default_permissions(administrator=True)
async def slash_setup_sangare(interaction: discord.Interaction):
    await setup_team_helper(interaction, "Sangaré")


@bot.tree.command(name="setup_muntari", description="Crée le tableau de bord pour l'équipe Muntari")
@app_commands.default_permissions(administrator=True)
async def slash_setup_muntari(interaction: discord.Interaction):
    await setup_team_helper(interaction, "Muntari")


@bot.tree.command(name="setup_reunification", description="Crée le tableau de bord pour la Réunification")
@app_commands.default_permissions(administrator=True)
async def slash_setup_reunification(interaction: discord.Interaction):
    await setup_team_helper(interaction, "Réunification")


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
            if not config["category_id"]:
                continue
            category = interaction.guild.get_channel(config["category_id"])
            if category and isinstance(category, discord.CategoryChannel):
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

    clean_name = found_room_name.replace("-rouge", "").replace("-jaune", "").replace("-reunifiee", "").replace("-reunifie", "").capitalize()
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


# ==========================================================
# 🎮 MINI-JEUX
# ==========================================================
# /jeux        : liste des jeux
# /totem       : devine le nombre secret (plus haut / plus bas)
# /qui_a_dit   : devine qui a écrit une phrase tirée de salons au hasard
# /qui_a_dit_spectateurs : idem, avec tous les salons visibles par les spectateurs
# /rapidite    : le premier à cliquer sur le bouton gagne
# /anagramme   : remets les lettres dans l'ordre
# /duel        : pierre-feuille-machette entre deux joueurs
# /reset_citations : (admin/orga) remet à zéro les phrases déjà utilisées
# ==========================================================

# Salons où une partie est déjà en cours (évite les parties qui se chevauchent)
parties_en_cours = set()


def normaliser(texte: str) -> str:
    """Minuscule, sans accents, sans espaces autour : pour comparer des mots."""
    texte = unicodedata.normalize("NFD", texte.strip().lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def format_scores(scores: dict) -> str:
    """Classement lisible à partir de {user_id: points}."""
    if not scores:
        return "*Personne n'a marqué de point.*"
    medailles = ["🥇", "🥈", "🥉"]
    classement = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lignes = []
    for i, (uid, pts) in enumerate(classement):
        icone = medailles[i] if i < 3 else "▫️"
        lignes.append(f"{icone} <@{uid}> — **{pts}** pt(s)")
    return "\n".join(lignes)


async def reserver_salon(interaction: discord.Interaction) -> bool:
    """Réserve le salon pour une partie. Renvoie False (et répond) s'il est déjà pris."""
    if interaction.channel.id in parties_en_cours:
        await interaction.response.send_message("⛔ Une partie est déjà en cours dans ce salon !", ephemeral=True)
        return False
    parties_en_cours.add(interaction.channel.id)
    return True


@bot.tree.command(name="jeux", description="Affiche la liste des mini-jeux disponibles")
async def slash_jeux(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎮 Mini-jeux du camp",
        description=(
            "🗿 `/totem` — Devine le nombre secret avant les autres\n"
            "🕵️ `/qui_a_dit` — Qui a dit cette phrase ? (citations tirées des salons)\n"
            "👁️ `/qui_a_dit_spectateurs` — Idem, mais avec les salons secrets (réservé aux spectateurs)\n"
            "⚡ `/rapidite` — Le plus rapide à cliquer gagne\n"
            "🔤 `/anagramme` — Remets les lettres dans l'ordre\n"
            "⚔️ `/duel` — Pierre-Feuille-Machette contre un autre joueur"
        ),
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)


# ------------------------------------------
# 🗿 LE TOTEM MAUDIT
# ------------------------------------------

@bot.tree.command(name="totem", description="🗿 Le Totem Maudit : devinez le nombre secret avant les autres !")
@app_commands.describe(maximum="Le nombre secret est entre 1 et ce maximum (défaut : 100)")
async def slash_totem(interaction: discord.Interaction, maximum: app_commands.Range[int, 10, 1000] = 100):
    if not await reserver_salon(interaction):
        return

    channel = interaction.channel
    secret = random.randint(1, maximum)
    duree = 90
    essais = {}

    embed = discord.Embed(
        title="🗿 LE TOTEM MAUDIT",
        description=(
            f"Un nombre secret est caché entre **1 et {maximum}**.\n"
            "Tapez simplement un nombre dans le salon !\n"
            "Je vous dirai **plus haut** ⬆️ ou **plus bas** ⬇️.\n\n"
            f"⏳ Vous avez **{duree} secondes**. Le premier à trouver gagne l'immunité ! 🔥"
        ),
        color=discord.Color.dark_orange()
    )
    await interaction.response.send_message(embed=embed)

    loop = asyncio.get_running_loop()
    fin = loop.time() + duree
    gagnant = None

    def check(m):
        return m.channel.id == channel.id and not m.author.bot and m.content.strip().isdigit()

    try:
        while True:
            reste = fin - loop.time()
            if reste <= 0:
                break
            try:
                msg = await bot.wait_for("message", check=check, timeout=reste)
            except asyncio.TimeoutError:
                break

            guess = int(msg.content.strip())
            essais[msg.author.id] = essais.get(msg.author.id, 0) + 1

            if guess == secret:
                gagnant = msg.author
                break
            elif guess < secret:
                await msg.reply("⬆️ **Plus haut !**", mention_author=False)
            else:
                await msg.reply("⬇️ **Plus bas !**", mention_author=False)
    finally:
        parties_en_cours.discard(channel.id)

    if gagnant:
        result = discord.Embed(
            title="🏆 LE TOTEM EST TROUVÉ !",
            description=f"{gagnant.mention} a trouvé **{secret}** en **{essais[gagnant.id]}** essai(s) !\n🔥 Immunité (virtuelle) pour toi !",
            color=discord.Color.green()
        )
    else:
        result = discord.Embed(
            title="💀 Le totem reste maudit...",
            description=f"Personne n'a trouvé. Le nombre était **{secret}**.",
            color=discord.Color.dark_red()
        )
    await channel.send(embed=result)


# ------------------------------------------
# 🕵️ QUI A DIT ÇA ?
# ------------------------------------------

def load_used_quotes() -> set:
    try:
        with open(USED_QUOTES_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_used_quotes(used: set):
    try:
        with open(USED_QUOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(list(used), f)
    except OSError as e:
        print(f"⚠️ Impossible de sauvegarder les citations utilisées : {e}")


used_quotes = load_used_quotes()


def is_valid_quote(msg: discord.Message, candidat_role) -> bool:
    """Une phrase est utilisable si elle vient d'un candidat, est lisible et jamais utilisée."""
    if msg.author.bot or msg.id in used_quotes:
        return False
    # L'auteur doit être (ou avoir été) candidat : rôle candidat OU rôle éliminé
    if not isinstance(msg.author, discord.Member):
        return False
    if not any(r.id in (CANDIDAT_ROLE_ID, ELIMINE_ROLE_ID) for r in msg.author.roles):
        return False
    text = msg.content.strip()
    if not (15 <= len(text) <= 250):
        return False
    if "http" in text.lower() or "@" in text or msg.mentions or msg.attachments:
        return False
    if text.startswith(("/", "!", "?", ".")):
        return False
    return True


def get_spectateur_role(guild):
    """Retrouve le rôle spectateurs."""
    return guild.get_role(SPECTATEUR_ROLE_ID)


def get_orga_role(guild):
    """Retrouve le rôle orga (par ID, sinon par nom contenant 'orga')."""
    if ORGA_ROLE_ID:
        return guild.get_role(ORGA_ROLE_ID)
    for role in guild.roles:
        if "orga" in normaliser(role.name):
            return role
    return None


def is_staff(member) -> bool:
    """Admin du serveur ou membre du rôle orga : peut tout lancer."""
    if member.guild_permissions.administrator:
        return True
    orga_role = get_orga_role(member.guild)
    return bool(orga_role and orga_role in member.roles)


def quote_channels(guild, candidat_role, only_channel=None, access_role=None):
    """Salons autorisés pour piocher une phrase.

    Mode normal (access_role=None) : jamais les salons secrets ni le salon admin,
    uniquement les salons visibles par les candidats.
    Mode spectateurs (access_role=rôle) : tous les salons que ce rôle peut voir
    (salons secrets inclus), et seulement ceux-là.
    """
    if only_channel:
        return [only_channel]
    result = []
    for ch in guild.text_channels:
        if access_role is None:
            if ch.category_id in QUOTE_EXCLUDED_CATEGORY_IDS or ch.id == ADMIN_LOG_CHANNEL_ID:
                continue
            # Uniquement les salons visibles par les candidats (évite de citer des salons privés/staff)
            if not ch.permissions_for(candidat_role).read_messages:
                continue
        else:
            # Uniquement les salons auxquels les spectateurs ont accès
            if not ch.permissions_for(access_role).read_messages:
                continue
        perms = ch.permissions_for(guild.me)
        if perms.read_messages and perms.read_message_history:
            result.append(ch)
    return result


async def pick_quote(guild, candidat_role, only_channel=None, access_role=None):
    """Choisit une phrase au hasard dans un salon au hasard, à une date au hasard."""
    channels = quote_channels(guild, candidat_role, only_channel, access_role)
    random.shuffle(channels)
    now = datetime.datetime.now(datetime.timezone.utc)

    for ch in channels[:25]:
        start = ch.created_at
        span = (now - start).total_seconds()
        # Plusieurs tentatives à des dates différentes dans le même salon
        for _ in range(3):
            random_date = start + datetime.timedelta(seconds=random.random() * span)
            try:
                batch = [m async for m in ch.history(limit=100, after=random_date)]
            except (discord.Forbidden, discord.HTTPException):
                break
            valid = [m for m in batch if is_valid_quote(m, candidat_role)]
            if valid:
                return random.choice(valid)
    return None


class QuiADitView(View):
    def __init__(self, options_members, voter_role_ids=None):
        super().__init__(timeout=None)
        self.votes = {}
        # si défini, seuls ces rôles (et les admins/orgas) peuvent voter
        self.voter_role_ids = voter_role_ids

        options = [
            discord.SelectOption(label=m.display_name[:100], value=str(m.id))
            for m in options_members
        ]
        self.select = Select(placeholder="👤 Qui a dit ça ?", options=options)
        self.select.callback = self.on_vote
        self.add_item(self.select)

    async def on_vote(self, interaction: discord.Interaction):
        if self.voter_role_ids is not None:
            user = interaction.user
            autorise = is_staff(user) or any(r.id in self.voter_role_ids for r in user.roles)
            if not autorise:
                await interaction.response.send_message(
                    "⛔ Seuls les spectateurs, les orgas et les admins peuvent voter ici.", ephemeral=True
                )
                return
        self.votes[interaction.user.id] = int(interaction.data["values"][0])
        await interaction.response.send_message(
            "🗳️ Vote enregistré ! Tu peux le changer jusqu'à la fin du temps imparti.", ephemeral=True
        )


async def lancer_qui_a_dit(interaction: discord.Interaction, manches: int, temps: int, salon, spectateurs: bool):
    """Moteur commun de /qui_a_dit (normal) et /qui_a_dit_spectateurs (accès à tout ce que voient les spectateurs)."""
    guild = interaction.guild
    candidat_role = guild.get_role(CANDIDAT_ROLE_ID)
    if not candidat_role:
        await interaction.response.send_message("❌ Rôle candidat introuvable.", ephemeral=True)
        return

    spectateur_role = None
    if spectateurs:
        spectateur_role = get_spectateur_role(guild)
        if not spectateur_role:
            await interaction.response.send_message(
                "❌ Rôle spectateurs introuvable. Renseigne `SPECTATEUR_ROLE_ID` dans le code ou les variables d'environnement.",
                ephemeral=True
            )
            return
        # Réservé aux spectateurs, orgas et admins (le contenu des salons secrets ne doit pas fuiter vers les candidats)
        staff = is_staff(interaction.user)
        if not staff and spectateur_role not in interaction.user.roles:
            await interaction.response.send_message("⛔ Ce jeu est réservé aux **spectateurs**, orgas et admins.", ephemeral=True)
            return
        # Le jeu ne doit pas se lancer dans un salon lisible par les candidats (sauf pour les orgas/admins)
        if not staff and interaction.channel.permissions_for(candidat_role).read_messages:
            await interaction.response.send_message(
                "⛔ Lance ce jeu dans un salon que les candidats ne peuvent pas voir (salon des spectateurs).",
                ephemeral=True
            )
            return
        if salon and not salon.permissions_for(spectateur_role).read_messages:
            await interaction.response.send_message("⛔ Les spectateurs n'ont pas accès à ce salon.", ephemeral=True)
            return
    else:
        # Si un salon précis est demandé, il ne doit pas être secret
        if salon and (salon.category_id in QUOTE_EXCLUDED_CATEGORY_IDS or salon.id == ADMIN_LOG_CHANNEL_ID):
            await interaction.response.send_message("⛔ Ce salon ne peut pas être utilisé pour ce jeu.", ephemeral=True)
            return

    # Joueurs proposés comme réponses : candidats + éliminés (anciens candidats)
    elimine_role = guild.get_role(ELIMINE_ROLE_ID)
    candidats = {m.id: m for m in candidat_role.members if not m.bot}
    if elimine_role:
        candidats.update({m.id: m for m in elimine_role.members if not m.bot})
    candidats = list(candidats.values())
    if len(candidats) < 4:
        await interaction.response.send_message("❌ Pas assez de candidats pour jouer.", ephemeral=True)
        return

    if not await reserver_salon(interaction):
        return

    titre_jeu = "QUI A DIT ÇA ? (SPECTATEURS)" if spectateurs else "QUI A DIT ÇA ?"
    await interaction.response.send_message(
        f"🕵️ **{titre_jeu}** — {manches} manche(s), {temps}s pour voter à chaque fois.\n"
        "Je cherche des phrases dans les salons du serveur... 🔎"
    )

    channel = interaction.channel
    scores = {}
    manches_jouees = 0

    try:
        for numero in range(1, manches + 1):
            quote = await pick_quote(guild, candidat_role, salon, spectateur_role)
            if not quote:
                await channel.send("😕 Je n'ai plus de nouvelle phrase à te proposer pour l'instant.")
                break

            used_quotes.add(quote.id)
            save_used_quotes(used_quotes)

            auteur = quote.author
            autres = [m for m in candidats if m.id != auteur.id]
            choix = random.sample(autres, min(9, len(autres))) + [auteur]
            random.shuffle(choix)

            view = QuiADitView(choix, voter_role_ids={spectateur_role.id} if spectateur_role else None)
            embed = discord.Embed(
                title=f"🕵️ Manche {numero}/{manches} — Qui a dit ça ?",
                description=f"> {discord.utils.escape_mentions(quote.content.strip())}\n\n⏳ **{temps} secondes** pour voter !",
                color=discord.Color.blurple()
            )
            msg = await channel.send(embed=embed, view=view)
            await asyncio.sleep(temps)

            view.select.disabled = True
            try:
                await msg.edit(view=view)
            except discord.HTTPException:
                pass

            bons = [uid for uid, vote in view.votes.items() if vote == auteur.id]
            for uid in bons:
                scores[uid] = scores.get(uid, 0) + 1

            gagnants_txt = ", ".join(f"<@{uid}>" for uid in bons) if bons else "*Personne*"
            reveal = discord.Embed(
                title="🎭 Réponse",
                description=(
                    f"C'était **{auteur.display_name}** ({auteur.mention}) !\n"
                    f"📍 Dans {quote.channel.mention} — [voir le message]({quote.jump_url})\n\n"
                    f"✅ Bonne réponse : {gagnants_txt}"
                ),
                color=discord.Color.green() if bons else discord.Color.red()
            )
            await channel.send(embed=reveal)
            manches_jouees += 1
            await asyncio.sleep(3)
    finally:
        parties_en_cours.discard(channel.id)

    if manches_jouees:
        await channel.send(embed=discord.Embed(
            title="🏆 Classement final — Qui a dit ça ?",
            description=format_scores(scores),
            color=discord.Color.gold()
        ))


@bot.tree.command(name="qui_a_dit", description="🕵️ Devine qui a prononcé une phrase tirée des salons du serveur")
@app_commands.describe(
    manches="Nombre de phrases (défaut : 3)",
    temps="Secondes pour voter à chaque manche (défaut : 30)",
    salon="Piocher uniquement dans ce salon (optionnel)"
)
async def slash_qui_a_dit(
    interaction: discord.Interaction,
    manches: app_commands.Range[int, 1, 10] = 3,
    temps: app_commands.Range[int, 10, 120] = 30,
    salon: discord.TextChannel = None
):
    await lancer_qui_a_dit(interaction, manches, temps, salon, spectateurs=False)


@bot.tree.command(name="qui_a_dit_spectateurs", description="👁️ Qui a dit ça ? Version spectateurs : phrases de tous les salons que les spectateurs voient")
@app_commands.describe(
    manches="Nombre de phrases (défaut : 3)",
    temps="Secondes pour voter à chaque manche (défaut : 30)",
    salon="Piocher uniquement dans ce salon (optionnel)"
)
async def slash_qui_a_dit_spectateurs(
    interaction: discord.Interaction,
    manches: app_commands.Range[int, 1, 10] = 3,
    temps: app_commands.Range[int, 10, 120] = 30,
    salon: discord.TextChannel = None
):
    await lancer_qui_a_dit(interaction, manches, temps, salon, spectateurs=True)


@bot.tree.command(name="reset_citations", description="Remet à zéro la liste des phrases déjà utilisées dans /qui_a_dit")
async def slash_reset_citations(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("⛔ Réservé aux admins et aux orgas.", ephemeral=True)
        return
    used_quotes.clear()
    save_used_quotes(used_quotes)
    await interaction.response.send_message("♻️ Les phrases pourront de nouveau être tirées.", ephemeral=True)


# ------------------------------------------
# ⚡ ÉPREUVE DE RAPIDITÉ
# ------------------------------------------

class RapiditeView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.winner = None
        self.start = None
        self.event = asyncio.Event()
        self.elapsed = None

        self.btn = Button(label="⏳ Attends...", style=discord.ButtonStyle.secondary, disabled=True)
        self.btn.callback = self.on_click
        self.add_item(self.btn)

    async def on_click(self, interaction: discord.Interaction):
        if self.winner or self.start is None:
            await interaction.response.defer()
            return
        self.winner = interaction.user
        self.elapsed = asyncio.get_running_loop().time() - self.start
        self.btn.disabled = True
        self.btn.label = f"🏆 {interaction.user.display_name}"
        self.btn.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=self)
        self.event.set()


@bot.tree.command(name="rapidite", description="⚡ Épreuve de rapidité : le plus rapide à cliquer gagne !")
@app_commands.describe(manches="Nombre de manches (défaut : 3)")
async def slash_rapidite(interaction: discord.Interaction, manches: app_commands.Range[int, 1, 10] = 3):
    if not await reserver_salon(interaction):
        return

    channel = interaction.channel
    scores = {}
    await interaction.response.send_message(
        f"⚡ **ÉPREUVE DE RAPIDITÉ** — {manches} manche(s) ! Quand le bouton devient vert, cliquez le plus vite possible !"
    )

    try:
        for numero in range(1, manches + 1):
            await asyncio.sleep(2)
            view = RapiditeView()
            msg = await channel.send(f"🔥 **Manche {numero}/{manches}** — Préparez-vous...", view=view)

            await asyncio.sleep(random.uniform(3, 9))

            view.btn.disabled = False
            view.btn.label = "🔥 CLIQUE !"
            view.btn.style = discord.ButtonStyle.danger
            view.start = asyncio.get_running_loop().time()
            await msg.edit(content=f"🔥 **Manche {numero}/{manches}** — **GO !**", view=view)

            try:
                await asyncio.wait_for(view.event.wait(), timeout=15)
            except asyncio.TimeoutError:
                view.btn.disabled = True
                view.btn.label = "💤 Trop tard"
                await msg.edit(content=f"💤 **Manche {numero}/{manches}** — Personne n'a réagi.", view=view)
                continue

            scores[view.winner.id] = scores.get(view.winner.id, 0) + 1
            await msg.edit(
                content=f"🏆 **Manche {numero}/{manches}** — {view.winner.mention} gagne en **{int(view.elapsed * 1000)} ms** !"
            )
    finally:
        parties_en_cours.discard(channel.id)

    await channel.send(embed=discord.Embed(
        title="🏆 Classement — Rapidité",
        description=format_scores(scores),
        color=discord.Color.gold()
    ))


# ------------------------------------------
# 🔤 ANAGRAMME
# ------------------------------------------

MOTS_ANAGRAMME = [
    "machette", "collier", "immunite", "conseil", "aventurier", "bivouac", "radeau", "cocotier",
    "totem", "tribu", "idole", "epreuve", "reunification", "pirogue", "naufrage", "campement",
    "feu", "braises", "allumette", "poisson", "banane", "noix", "coco", "jungle", "plage", "foret",
    "riviere", "marmite", "hamac", "boussole", "carte", "oasis", "lagon", "rocher", "falaise",
    "strategie", "alliance", "trahison", "vote", "jury", "finale", "poteaux", "equilibre", "orientation",
    "survie", "faim", "soif", "moustique", "orage", "tempete", "cabane", "bambou", "liane", "sable",
    "recif", "mangrove", "volcan", "ananas", "cascade", "sentier", "tresor", "indice", "avantage",
]
mots_utilises = set()


def tirer_mot() -> str:
    global mots_utilises
    disponibles = [m for m in MOTS_ANAGRAMME if m not in mots_utilises]
    if not disponibles:
        mots_utilises = set()
        disponibles = list(MOTS_ANAGRAMME)
    mot = random.choice(disponibles)
    mots_utilises.add(mot)
    return mot


def melanger(mot: str) -> str:
    lettres = list(mot)
    for _ in range(20):
        random.shuffle(lettres)
        if "".join(lettres) != mot:
            break
    return "".join(lettres)


@bot.tree.command(name="anagramme", description="🔤 Remets les lettres dans l'ordre pour trouver le mot !")
@app_commands.describe(manches="Nombre de mots (défaut : 5)")
async def slash_anagramme(interaction: discord.Interaction, manches: app_commands.Range[int, 1, 15] = 5):
    if not await reserver_salon(interaction):
        return

    channel = interaction.channel
    scores = {}
    await interaction.response.send_message(
        f"🔤 **ANAGRAMME** — {manches} mot(s) à retrouver ! Écrivez la réponse directement dans le salon (20 secondes par mot)."
    )

    try:
        for numero in range(1, manches + 1):
            await asyncio.sleep(2)
            mot = tirer_mot()
            melange = melanger(mot)
            await channel.send(embed=discord.Embed(
                title=f"🔤 Mot {numero}/{manches}",
                description=f"# {' '.join(melange.upper())}\n*({len(mot)} lettres)*",
                color=discord.Color.teal()
            ))

            loop = asyncio.get_running_loop()
            fin = loop.time() + 20
            gagnant = None

            def check(m):
                return m.channel.id == channel.id and not m.author.bot and normaliser(m.content) == mot

            try:
                msg = await bot.wait_for("message", check=check, timeout=20)
                gagnant = msg.author
                await msg.add_reaction("✅")
            except asyncio.TimeoutError:
                pass

            if gagnant:
                scores[gagnant.id] = scores.get(gagnant.id, 0) + 1
                await channel.send(f"✅ {gagnant.mention} trouve **{mot.upper()}** !")
            else:
                await channel.send(f"⌛ Temps écoulé ! Le mot était **{mot.upper()}**.")
    finally:
        parties_en_cours.discard(channel.id)

    await channel.send(embed=discord.Embed(
        title="🏆 Classement — Anagramme",
        description=format_scores(scores),
        color=discord.Color.gold()
    ))


# ------------------------------------------
# ⚔️ DUEL : PIERRE - FEUILLE - MACHETTE
# ------------------------------------------

DUEL_EMOJIS = {"pierre": "🪨 Pierre", "feuille": "🍃 Feuille", "machette": "🔪 Machette"}
DUEL_BEATS = {"pierre": "machette", "machette": "feuille", "feuille": "pierre"}


class DuelView(View):
    def __init__(self, p1: discord.Member, p2: discord.Member):
        super().__init__(timeout=None)
        self.players = {p1.id: p1, p2.id: p2}
        self.choices = {}
        self.done = asyncio.Event()

    async def _choose(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in self.players:
            await interaction.response.send_message("⛔ Ce duel ne te concerne pas !", ephemeral=True)
            return
        if uid in self.choices:
            await interaction.response.send_message("ℹ️ Tu as déjà fait ton choix.", ephemeral=True)
            return
        self.choices[uid] = choice
        await interaction.response.send_message(f"✅ Tu as choisi **{DUEL_EMOJIS[choice]}**. En attente de l'adversaire...", ephemeral=True)
        if len(self.choices) == 2:
            self.done.set()

    @discord.ui.button(label="🪨 Pierre", style=discord.ButtonStyle.secondary)
    async def pierre(self, interaction: discord.Interaction, button: Button):
        await self._choose(interaction, "pierre")

    @discord.ui.button(label="🍃 Feuille", style=discord.ButtonStyle.success)
    async def feuille(self, interaction: discord.Interaction, button: Button):
        await self._choose(interaction, "feuille")

    @discord.ui.button(label="🔪 Machette", style=discord.ButtonStyle.danger)
    async def machette(self, interaction: discord.Interaction, button: Button):
        await self._choose(interaction, "machette")


@bot.tree.command(name="duel", description="⚔️ Défie un joueur à Pierre-Feuille-Machette")
@app_commands.describe(adversaire="Le joueur à défier")
async def slash_duel(interaction: discord.Interaction, adversaire: discord.Member):
    if adversaire.bot or adversaire.id == interaction.user.id:
        await interaction.response.send_message("❌ Choisis un autre joueur humain pour ton duel !", ephemeral=True)
        return

    p1, p2 = interaction.user, adversaire
    view = DuelView(p1, p2)
    await interaction.response.send_message(
        f"⚔️ **DUEL** : {p1.mention} VS {p2.mention}\nChoisissez votre arme en secret (60 secondes) !",
        view=view
    )
    msg = await interaction.original_response()

    try:
        await asyncio.wait_for(view.done.wait(), timeout=60)
    except asyncio.TimeoutError:
        for child in view.children:
            child.disabled = True
        await msg.edit(content="⌛ Duel annulé : tout le monde n'a pas joué à temps.", view=view)
        return

    for child in view.children:
        child.disabled = True

    c1, c2 = view.choices[p1.id], view.choices[p2.id]
    resume = f"{p1.mention} : **{DUEL_EMOJIS[c1]}**\n{p2.mention} : **{DUEL_EMOJIS[c2]}**\n\n"
    if c1 == c2:
        resume += "🤝 **Égalité !** Relancez un duel."
    elif DUEL_BEATS[c1] == c2:
        resume += f"🏆 **{p1.display_name}** remporte le duel !"
    else:
        resume += f"🏆 **{p2.display_name}** remporte le duel !"

    await msg.edit(content=f"⚔️ **DUEL TERMINÉ**\n{resume}", view=view)


bot.run(TOKEN)
