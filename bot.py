import discord
from discord.ext import commands
from discord.ui import View, Select, Button
import io

# --- CONFIGURATION ---
TOKEN = "TON_TOKEN_DISCORD_ICI"
ADMIN_LOG_CHANNEL_ID = 123456789012345678  # Remplace par l'ID de ton salon admin
CATEGORY_ID = None  # (Optionnel) ID de la catégorie où regrouper ces salons

# Salons prédéfinis avec leurs limites respectives
PRESET_ROOMS_CONFIG = {
    "point-d-eau": 3,
    "foret": 3,
    "plage": 4,
    "riviere": 5
}

# Structure en mémoire : {channel_id: {"name": str, "capacity": int, "members": [user_ids]}}
rooms_data = {}
dashboard_message = None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# --- FONCTIONS UTILITAIRES ---

async def archive_and_purge(channel, guild, reason_text):
    """Archive tous les messages vers le salon admin puis vide le salon."""
    admin_channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
    messages = [msg async for msg in channel.history(limit=200, oldest_first=True)]
    
    if messages and admin_channel:
        log_text = f"=== ARCHIVE : {channel.name} ===\nÉvénement : {reason_text}\n\n"
        for msg in messages:
            if not msg.author.bot:
                log_text += f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author.name} : {msg.content}\n"
        
        file = discord.File(
            fp=io.BytesIO(log_text.encode('utf-8')), 
            filename=f"log_{channel.name}_{msg.created_at.strftime('%d%m%Y_%H%M%S')}.txt"
        )
        await admin_channel.send(content=f"📁 **Archive de {channel.mention}** (`{reason_text}`)", file=file)
    
    # Nettoie le salon
    await channel.purge(limit=200)


def generate_dashboard_embed(guild):
    """Génère le tableau de bord avec l'état de chaque zone."""
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
    """Met à jour le message du Dashboard en temps réel."""
    global dashboard_message
    if dashboard_message:
        try:
            await dashboard_message.edit(embed=generate_dashboard_embed(guild), view=DashboardView(guild))
        except Exception as e:
            print(f"Erreur d'actualisation du dashboard: {e}")


# --- VUE INTERACTIVE (MENU ET BOUTON) ---

class DashboardView(View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild
        
        # Menu déroulant listant tous les lieux fixes
        options = []
        for ch_id, data in rooms_data.items():
            nb = len(data["members"])
            cap = data["capacity"]
            label_txt = f"{data['name'].capitalize()} ({nb}/{cap})"
            desc = "Complet !" if nb >= cap else f"Rejoindre ce lieu ({cap - nb} place(s) restante(s))"
            
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
                custom_id="select_location"
            )
            select.callback = self.join_room_callback
            self.add_item(select)

    async def join_room_callback(self, interaction: discord.Interaction):
        user = interaction.user
        target_channel_id = int(interaction.data["values"][0])
        
        if target_channel_id not in rooms_data:
            await interaction.response.send_message("❌ Ce salon est introuvable.", ephemeral=True)
            return

        room = rooms_data[target_channel_id]
        channel = self.guild.get_channel(target_channel_id)

        # 1. Vérifications préalables
        if user.id in room["members"]:
            await interaction.response.send_message("ℹ️ Vous êtes déjà dans ce lieu.", ephemeral=True)
            return

        for data in rooms_data.values():
            if user.id in data["members"]:
                await interaction.response.send_message("❌ Vous êtes déjà dans un autre lieu ! Quittez-le avant d'en changer.", ephemeral=True)
                return

        if len(room["members"]) >= room["capacity"]:
            await interaction.response.send_message("⛔ Ce lieu a atteint sa capacité maximale.", ephemeral=True)
            return

        # 2. Archive et purge avant que le nouvel arrivant ne puisse lire
        await archive_and_purge(channel, self.guild, f"{user.name} a REJOINT {room['name']}")

        # 3. Donner l'accès au nouveau candidat
        room["members"].append(user.id)
        await channel.set_permissions(user, read_messages=True, send_messages=True, read_message_history=True)

        await interaction.response.send_message(f"✅ Vous avez rejoint le salon {channel.mention} !", ephemeral=True)
        await refresh_dashboard(self.guild)

    @discord.ui.button(label="🚪 Quitter mon lieu actuel", style=discord.ButtonStyle.danger, custom_id="btn_leave_location")
    async def leave_btn(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        current_ch_id = None

        for ch_id, data in rooms_data.items():
            if user.id in data["members"]:
                current_ch_id = ch_id
                break

        if not current_ch_id:
            await interaction.response.send_message("ℹ️ Vous n'êtes actuellement dans aucun lieu.", ephemeral=True)
            return

        channel = self.guild.get_channel(current_ch_id)
        room = rooms_data[current_ch_id]

        # 1. Archive et purge
        if channel:
            await archive_and_purge(channel, self.guild, f"{user.name} a QUITTÉ {room['name']}")
            await channel.set_permissions(user, overwrite=None)

        # 2. Retirer l'utilisateur
        room["members"].remove(user.id)

        await interaction.response.send_message("✅ Vous avez quitté le lieu.", ephemeral=True)
        await refresh_dashboard(self.guild)


# --- INITIALISATION ET COMMANDES ADMIN ---

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    """Commande à taper pour initialiser les salons et afficher la carte."""
    global dashboard_message, rooms_data
    await ctx.message.delete()
    
    category = ctx.guild.get_channel(CATEGORY_ID) if CATEGORY_ID else None
    
    # Création ou détection des 4 salons prédéfinis
    for name, capacity in PRESET_ROOMS_CONFIG.items():
        # Chercher si le salon existe déjà
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

    # Envoi du message du tableau de bord
    embed = generate_dashboard_embed(ctx.guild)
    view = DashboardView(ctx.guild)
    dashboard_message = await ctx.send(embed=embed, view=view)

@bot.event
async def on_ready():
    print(f"🤖 Bot prêt et connecté en tant que : {bot.user}")

bot.run(TOKEN)
