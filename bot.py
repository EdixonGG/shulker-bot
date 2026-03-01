import os
import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import date, timedelta
import time

# ===============================
# CONFIGURACIÓN
# ===============================
FORM_CHANNEL_ID = 1465764092978532547
RANKING_CHANNEL_ID = 1468791225619320894
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker_mensual (
    user_id INTEGER,
    username TEXT,
    mes TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, mes)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker_cerrado (
    mes TEXT PRIMARY KEY,
    fecha_cierre TEXT,
    total_general INTEGER
)
""")

db.commit()
cooldowns = {}

# ===============================
# UTILIDADES
# ===============================
def calcular_equivalencias(total_shulkers):
    stacks = total_shulkers * 27
    bloques = stacks * 64
    niveles = bloques * 9
    pv = total_shulkers // 27
    resto = total_shulkers % 27
    return stacks, bloques, niveles, pv, resto

def fecha_linda(fecha_iso):
    meses = {
        "01": "Enero", "02": "Febrero", "03": "Marzo",
        "04": "Abril", "05": "Mayo", "06": "Junio",
        "07": "Julio", "08": "Agosto", "09": "Septiembre",
        "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
    }
    y, m, d = fecha_iso.split("-")
    return f"{int(d)} de {meses[m]} del {y}"

def actualizar_mensual(user_id, username, cantidad):
    mes = date.today().strftime("%Y-%m")
    cursor.execute(
        "SELECT total FROM shulker_mensual WHERE user_id = ? AND mes = ?",
        (user_id, mes)
    )
    row = cursor.fetchone()
    nuevo = cantidad if not row else row[0] + cantidad

    cursor.execute(
        "REPLACE INTO shulker_mensual (user_id, username, mes, total) VALUES (?, ?, ?, ?)",
        (user_id, username, mes, nuevo)
    )
    db.commit()

def cerrar_mes_si_corresponde():
    hoy = date.today()
    mes_anterior = (hoy.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    cursor.execute("SELECT mes FROM shulker_cerrado WHERE mes = ?", (mes_anterior,))
    if cursor.fetchone():
        return

    cursor.execute(
        "SELECT SUM(total) FROM shulker_mensual WHERE mes = ?",
        (mes_anterior,)
    )
    total = cursor.fetchone()[0] or 0

    cursor.execute(
        "INSERT INTO shulker_cerrado (mes, fecha_cierre, total_general) VALUES (?, ?, ?)",
        (mes_anterior, str(date.today()), total)
    )
    db.commit()

# ===============================
# EMBEDS
# ===============================
async def crear_embed_ranking(titulo, emoji, color, datos, footer):
    descripcion = ""
    for i, (user, total) in enumerate(datos, start=1):
        medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
        descripcion += f"{medalla} **{i}. {user}** — `{total}` shulker\n"

    total_shulkers = sum(t for _, t in datos)
    stacks, bloques, niveles, pv, resto = calcular_equivalencias(total_shulkers)

    descripcion += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        f"📦 **Total:** `{total_shulkers}` shulkers\n"
        f"📦 **Stacks:** `{stacks}`\n"
        f"🧱 **Bloques End:** `{bloques}`\n"
        f"📈 **Niveles de Isla:** `{niveles}`\n"
        f"🚀 **Equivalente:** `{pv} PV` + `{resto}` shulkers"
    )

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=descripcion or "_Sin registros_",
        color=color
    )
    embed.set_footer(text=footer)
    return embed

async def crear_embed_mes_cerrado(mes):
    cursor.execute("""
        SELECT username, total
        FROM shulker_mensual
        WHERE mes = ?
        ORDER BY total DESC
        LIMIT 10
    """, (mes,))
    datos = cursor.fetchall()

    total = sum(t for _, t in datos)
    stacks, bloques, niveles, pv, resto = calcular_equivalencias(total)
    fecha = fecha_linda(f"{mes}-01")

    descripcion = ""
    for i, (user, t) in enumerate(datos, start=1):
        descripcion += f"**#{i} {user}** — `{t}` shulker\n"

    descripcion += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        f"📦 **Total del Mes:** `{total}` shulkers\n"
        f"📈 **Niveles de Isla:** `{niveles}`\n"
        f"🚀 **Equivalente:** `{pv} PV` + `{resto}` shulkers"
    )

    embed = discord.Embed(
        title=f"🏆 TOP SHULKER — {fecha}",
        description=descripcion,
        color=discord.Color.dark_gold()
    )
    embed.set_footer(text="📁 Historial mensual")
    return embed

# ===============================
# RANKINGS
# ===============================
async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    hoy = date.today()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

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

    embeds = [
        await crear_embed_ranking("TOP MENSUAL", "👑", discord.Color.purple(), mensual, "Mes actual"),
        await crear_embed_ranking("TOP SEMANAL", "📈", discord.Color.blue(), semanal, "Semana actual"),
        await crear_embed_ranking("TOP DIARIO", "⚡", discord.Color.gold(), diario, "Hoy")
    ]

    async for msg in channel.history(limit=10):
        if msg.author == bot.user:
            await msg.delete()

    cursor.execute("SELECT mes FROM shulker_cerrado ORDER BY mes DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        await channel.send(embed=await crear_embed_mes_cerrado(row[0]))

    for embed in embeds:
        await channel.send(embed=embed)

# ===============================
# TASK
# ===============================
@tasks.loop(hours=1)
async def ranking_automatico():
    cerrar_mes_si_corresponde()
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
            await interaction.response.send_message("⏳ Espera antes de registrar.", ephemeral=True)
            return

        cooldowns[user_id] = ahora

        cantidad_int = int(self.cantidad.value)
        hoy = str(date.today())
        username = interaction.user.display_name

        cursor.execute(
            "SELECT total FROM shulker WHERE user_id = ? AND fecha = ?",
            (user_id, hoy)
        )
        row = cursor.fetchone()
        nuevo = cantidad_int if not row else row[0] + cantidad_int

        cursor.execute(
            "REPLACE INTO shulker VALUES (?, ?, ?, ?)",
            (user_id, username, hoy, nuevo)
        )
        db.commit()

        actualizar_mensual(user_id, username, cantidad_int)
        await actualizar_todos_los_ranking()

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
