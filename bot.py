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
RANKING_CHANNEL_ID = 1468791225619320894
REGLAS_CHANNEL_ID = 1462316362004434978
END_CHANNEL_ID = 1462316362515873947

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

# ===============================
# EMBED RANKING
# ===============================
async def crear_embed_ranking(titulo, emoji, color, datos, footer):
    if not datos:
        descripcion = "_Sin registros aún_"
    else:
        descripcion = ""
        for i, (user, total) in enumerate(datos, start=1):
            medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
            descripcion += f"{medalla} **{i}. {user}** — `{total}` shulker\n"

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=descripcion,
        color=color
    )
    embed.set_footer(text=footer)
    return embed

# ===============================
# ACTUALIZAR RANKINGS (ORDEN FIJO)
# ===============================
async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    hoy = date.today()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

    # ===== CONSULTAS =====
    cursor.execute("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, (str(inicio_mes),))
    mensual = cursor.fetchall()

    cursor.execute("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, (str(inicio_semana),))
    semanal = cursor.fetchall()

    cursor.execute("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha = ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, (str(hoy),))
    diario = cursor.fetchall()

    # ===== EMBEDS EN ORDEN CORRECTO =====
    embeds = [
        await crear_embed_ranking(
            "TOP MENSUAL",
            "👑",
            discord.Color.purple(),
            mensual,
            "🗓️ Mes actual"
        ),
        await crear_embed_ranking(
            "TOP SEMANAL",
            "📈",
            discord.Color.blue(),
            semanal,
            f"📅 Desde {inicio_semana}"
        ),
        await crear_embed_ranking(
            "TOP DIARIO",
            "⚡",
            discord.Color.gold(),
            diario,
            f"📆 Hoy • {hoy}"
        )
    ]

    # ===== LIMPIAR MENSAJES DEL BOT =====
    async for msg in channel.history(limit=10):
        if msg.author == bot.user:
            await msg.delete()

    # ===== PUBLICAR EN ORDEN =====
    for embed in embeds:
        await channel.send(embed=embed)

# ===============================
# TASK AUTOMÁTICO
# ===============================
@tasks.loop(minutes=5)
async def ranking_automatico():
    await actualizar_todos_los_ranking()

# ===============================
# MODAL
# ===============================
class ShulkerModal(discord.ui.Modal, title="Registro de Shulker"):
    cantidad = discord.ui.TextInput(label="¿Cuántas shulker colocaste?", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        ahora = time.time()

        if user_id in cooldowns and ahora - cooldowns[user_id] < COOLDOWN_SECONDS:
            await interaction.response.send_message(
                "⏳ Debes esperar antes de volver a registrar.",
                ephemeral=True
            )
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

        await actualizar_todos_los_ranking()

        end_channel = interaction.client.get_channel(END_CHANNEL_ID)
        if end_channel:
            embed = discord.Embed(
                title="📦 Registro de Shulker",
                description=(
                    f"👤 **Jugador:** {interaction.user.mention}\n"
                    f"➕ **Agregado:** {cantidad_int}\n"
                    f"📊 **Total hoy:** {nuevo_total}"
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="Buen trabajo 💪")
            await end_channel.send(embed=embed)

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

    if not ranking_automatico.is_running():
        ranking_automatico.start()

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
