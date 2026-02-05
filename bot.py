import os
import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import date, datetime, timedelta
import time

# ===============================
# CONFIGURACIÓN
# ===============================
FORM_CHANNEL_ID = 1465764092978532547
LOG_CHANNEL_ID = 1462316362515873947
RANKING_CHANNEL_ID = 1462316362515873948
REGLAS_CHANNEL_ID = 1462316362004434978

TOKEN = os.getenv("DISCORD_TOKEN")
COOLDOWN_SECONDS = 60

# ===============================
# INTENTS
# ===============================
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# ===============================
# BASE DE DATOS
# ===============================
db = sqlite3.connect("shulker.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker (
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER
)
""")
db.commit()

cooldowns = {}
ultimo_ranking_publicado = None

# ===============================
# RESET DIARIO
# ===============================
@tasks.loop(minutes=1)
async def reset_diario():
    hoy = str(date.today())
    cursor.execute("DELETE FROM shulker WHERE fecha != ?", (hoy,))
    db.commit()

# ===============================
# RANKING AUTOMÁTICO 23:59
# ===============================
@tasks.loop(minutes=1)
async def ranking_diario_automatico():
    global ultimo_ranking_publicado

    ahora = datetime.utcnow()
    hoy = ahora.date()

    if ahora.hour == 23 and ahora.minute == 59:
        if ultimo_ranking_publicado == hoy:
            return
        await actualizar_ranking(bot)
        ultimo_ranking_publicado = hoy

# ===============================
# FUNCIÓN RANKING DIARIO
# ===============================
async def actualizar_ranking(bot):
    hoy = str(date.today())

    cursor.execute("""
        SELECT username, total
        FROM shulker
        WHERE fecha = ?
        ORDER BY total DESC
    """, (hoy,))
    datos = cursor.fetchall()

    if not datos:
        return

    descripcion = ""
    for i, (user, total) in enumerate(datos, start=1):
        descripcion += f"**{i}. {user}** — {total} shulker\n"

    embed = discord.Embed(
        title="🏆 Ranking Diario de Shulker",
        description=descripcion,
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Fecha: {hoy}")

    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    async for msg in channel.history(limit=10):
        if msg.author == bot.user and msg.embeds:
            await msg.edit(embed=embed)
            return

    await channel.send(embed=embed)

# ===============================
# COMANDO !topdia (ADMIN)
# ===============================
@bot.command()
@commands.has_permissions(administrator=True)
async def topdia(ctx):
    hoy = str(date.today())

    cursor.execute("""
        SELECT username, total
        FROM shulker
        WHERE fecha = ?
        ORDER BY total DESC
    """, (hoy,))
    datos = cursor.fetchall()

    if not datos:
        await ctx.send("📭 No hay registros hoy.")
        return

    descripcion = ""
    for i, (user, total) in enumerate(datos, start=1):
        descripcion += f"**{i}. {user}** — {total} shulker\n"

    embed = discord.Embed(
        title="🏆 Top Diario de Shulker",
        description=descripcion,
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Fecha: {hoy}")

    await ctx.send(embed=embed)

# ===============================
# COMANDO !topsemana (ADMIN)
# ===============================
@bot.command()
@commands.has_permissions(administrator=True)
async def topsemana(ctx):
    hoy = date.today()
    desde = str(hoy - timedelta(days=6))

    cursor.execute("""
        SELECT username, SUM(total) as total_semana
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY total_semana DESC
    """, (desde,))
    datos = cursor.fetchall()

    if not datos:
        await ctx.send("📭 No hay registros esta semana.")
        return

    descripcion = ""
    for i, (user, total) in enumerate(datos, start=1):
        descripcion += f"**{i}. {user}** — {total} shulker\n"

    embed = discord.Embed(
        title="📆 Top Semanal de Shulker",
        description=descripcion,
        color=discord.Color.purple()
    )
    embed.set_footer(text="Últimos 7 días")

    await ctx.send(embed=embed)

# ===============================
# EMBED REGLAS
# ===============================
async def enviar_reglas(channel):
    embed = discord.Embed(
        title="📜 REGLAS OFICIALES DEL TEAM",
        description="Normas para mantener un ambiente ordenado, justo y sano.",
        color=discord.Color.gold()
    )

    embed.add_field(name="🤝 Respeto", value="Respeto total.\n❌ Insultos o discriminación", inline=False)
    embed.add_field(name="🧠 Comunicación", value="Usa cada canal correctamente.\n❌ Spam", inline=False)
    embed.add_field(name="📌 Canales importantes", value="Solo info oficial.", inline=False)
    embed.add_field(name="🧰 Aportes", value="Registros honestos.\n❌ Mentir datos", inline=False)
    embed.add_field(name="🛡️ Staff", value="Decisiones se respetan.", inline=False)
    embed.add_field(name="🚫 Prohibido", value="Traición, robo, filtrar info", inline=False)
    embed.add_field(name="⚖️ Sanciones", value="Advertencia → Restricción → Expulsión", inline=False)

    embed.set_footer(text="Permanecer en el servidor implica aceptar estas reglas")

    await channel.send(embed=embed)

# ===============================
# COMANDO PUBLICAR REGLAS
# ===============================
@bot.command()
@commands.has_permissions(administrator=True)
async def publicar_reglas(ctx):
    channel = bot.get_channel(REGLAS_CHANNEL_ID)
    if not channel:
        await ctx.send("❌ Canal de reglas no encontrado.")
        return

    await enviar_reglas(channel)
    await ctx.send("✅ Reglas publicadas.", delete_after=5)

# ===============================
# MODAL
# ===============================
class ShulkerModal(discord.ui.Modal, title="Registro de Shulker"):
    cantidad = discord.ui.TextInput(label="¿Cuántas shulker colocaste?", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        ahora = time.time()

        if user_id in cooldowns and ahora - cooldowns[user_id] < COOLDOWN_SECONDS:
            await interaction.response.send_message("⏳ Espera antes de registrar.", ephemeral=True)
            return

        cooldowns[user_id] = ahora

        try:
            cantidad_int = int(self.cantidad.value)
            if cantidad_int <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Número inválido.", ephemeral=True)
            return

        hoy = str(date.today())
        username = interaction.user.display_name

        cursor.execute(
            "SELECT total FROM shulker WHERE user_id = ? AND fecha = ?",
            (user_id, hoy)
        )
        row = cursor.fetchone()

        nuevo_total = cantidad_int if not row else row[0] + cantidad_int

        cursor.execute(
            "REPLACE INTO shulker (user_id, username, fecha, total) VALUES (?, ?, ?, ?)",
            (user_id, username, hoy, nuevo_total)
        )
        db.commit()

        await actualizar_ranking(interaction.client)
        await interaction.response.send_message("✅ Registro guardado.", ephemeral=True)

# ===============================
# BOTÓN
# ===============================
class ShulkerButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Registrar Shulker", style=discord.ButtonStyle.green, emoji="📦")
    async def registrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ShulkerModal())

# ===============================
# READY
# ===============================
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")

    if not reset_diario.is_running():
        reset_diario.start()

    if not ranking_diario_automatico.is_running():
        ranking_diario_automatico.start()

    channel = bot.get_channel(FORM_CHANNEL_ID)
    if channel:
        await channel.send(
            embed=discord.Embed(
                title="🧰 Registro de Shulker",
                description="Presiona el botón para registrar.",
                color=discord.Color.green()
            ),
            view=ShulkerButton()
        )

# ===============================
# RUN
# ===============================
bot.run(TOKEN)
