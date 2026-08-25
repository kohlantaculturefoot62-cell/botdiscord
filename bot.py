import discord
from discord.ext import commands
from discord.ui import View, Select, Button
import io

# --- CONFIGURATION ---
TOKEN = "TON_TOKEN_DISCORD_ICI"
ADMIN_LOG_CHANNEL_ID = 123456789012345678 # Remplace par l'ID de ton salon admin
MAX_CAPACITY = 3 # Limite max par salon

# Dictionnaire pour stocker l'état des salons : {channel_id: [liste_des_membres_id]}
active_rooms = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Fonction pour archiver et purger un salon
async def archive_and_purge(channel, guild, action_text):
    admin_channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
    
    # Récupérer l'historique des messages (du plus ancien au plus récent)
    messages = [msg async for msg in channel.history(limit=100, oldest_first=True)]
    
    if len(messages) > 0:
        # Formater les messages dans un texte
        log_text = f"--- ARCHIVE : {channel.name} ({action_text}) ---\n\n"
        for msg in messages:
            if not msg.author.bot: # Optionnel : ignorer les messages du bot
                log_text += f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author.name} : {msg.content}\n"
        
        # Créer un fichier texte en mémoire pour éviter la limite de caractères de Discord
        file = discord.File(fp=io.BytesIO(log_text.encode('utf-8')), filename=f"log_{channel.name}.txt")
        await admin_channel.send(content=f"📁 **Nouvelle archive de {channel.mention}** suite à : *{action_text}*", file=file)
    
    # Supprimer tous les messages du salon
    await channel.purge(limit=100)

# Fonction pour générer l'Embed (Tableau de bord)
def generate_dashboard_embed(guild):
    embed = discord.Embed(
        title="🎮 Hub de sélection des salons", 
        description="Choisissez un salon à rejoindre. Les discussions sont secrètes et s'effacent dès que quelqu'un entre ou sort !", 
        color=discord.Color.blurple()
    )
    
    for channel_id, members in active_rooms.items():
        channel = guild.get_channel(channel_id)
        if channel:
            # Créer la liste des noms ou afficher "Vide"
            member_names = [guild.get_member(m_id).mention for m_id in members if guild.get_member(m_id)]
            occupants = "\n".join(member_names) if member_names else "*Vide*"
            
            embed.add_field(
                name=f"🚪 {channel.name} ({len(members)}/{MAX_CAPACITY})", 
                value=occupants, 
                inline=True
            )
            
    return embed

# --- INTERFACE UTILISATEUR (Boutons et Menu) ---
class DashboardView(View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild
        
        # Menu déroulant pour choisir un salon
        options = []
        for channel_id in active_rooms.keys():
            channel = guild.get_channel(channel_id)
            if channel:
                options.append(discord.SelectOption(label=channel.name, value=str(channel_id), description=f"Rejoindre ce salon"))
        
        self.select = Select(placeholder="Où voulez-vous aller ?", options=options, custom_id="room_select")
        self.select.callback = self.join_callback
        self.add_item(self.select)

    async def join_callback(self, interaction: discord.Interaction):
        user = interaction.user
        target_channel_id = int(self.select.values[0])
        target_channel = self.guild.get_channel(target_channel_id)
        
        # Vérifier si l'utilisateur est déjà dans un salon
        for ch_id, members in active_rooms.items():
            if user.id in members:
                await interaction.response.send_message("Vous êtes déjà dans un salon ! Quittez-le d'abord.", ephemeral=True)
                return
                
        # Vérifier la capacité
        if len(active_rooms[target_channel_id]) >= MAX_CAPACITY:
            await interaction.response.send_message("Ce salon est plein !", ephemeral=True)
            return

        # Archiver et purger le salon AVANT que l'utilisateur n'y entre
        await archive_and_purge(target_channel, self.guild, f"{user.name} a rejoint")

        # Ajouter l'utilisateur au salon
        active_rooms[target_channel_id].append(user.id)
        
        # Modifier les permissions pour lui donner accès
        await target_channel.set_permissions(user, read_messages=True, send_messages=True)
        
        # Mettre à jour le tableau de bord
        await interaction.response.edit_message(embed=generate_dashboard_embed(self.guild), view=self)
        await user.send(f"Vous avez rejoint {target_channel.name} !")

    @discord.ui.button(label="🚪 Quitter mon salon", style=discord.ButtonStyle.danger, custom_id="leave_btn")
    async def leave_callback(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        current_room_id = None
        
        # Trouver dans quel salon est l'utilisateur
        for ch_id, members in active_rooms.items():
            if user.id in members:
                current_room_id = ch_id
                break
                
        if not current_room_id:
            await interaction.response.send_message("Vous n'êtes dans aucun salon.", ephemeral=True)
            return

        channel = self.guild.get_channel(current_room_id)
        
        # Archiver et purger le salon car quelqu'un part
        await archive_and_purge(channel, self.guild, f"{user.name} a quitté")
        
        # Retirer l'utilisateur du salon
        active_rooms[current_room_id].remove(user.id)
        
        # Retirer les permissions (le ramener à l'état par défaut)
        await channel.set_permissions(user, overwrite=None)
        
        # Mettre à jour le tableau de bord
        await interaction.response.edit_message(embed=generate_dashboard_embed(self.guild), view=self)
        await interaction.followup.send("Vous avez quitté le salon. Les messages ont été effacés.", ephemeral=True)


# --- COMMANDES D'ADMINISTRATION ---
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    # Créer 3 salons de test si la liste est vide
    if not active_rooms:
        for i in range(1, 4):
            # Le rôle @everyone ne peut pas voir/lire ces salons
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            new_channel = await ctx.guild.create_text_channel(f"salon-secret-{i}", overwrites=overwrites)
            active_rooms[new_channel.id] = []
            
    # Envoyer le tableau de bord
    embed = generate_dashboard_embed(ctx.guild)
    view = DashboardView(ctx.guild)
    await ctx.send(embed=embed, view=view)

@bot.event
async def on_ready():
    print(f'Connecté en tant que {bot.user}')

bot.run(TOKEN)
