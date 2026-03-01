import os
import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import date, timedelta
import time
import json

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
CREATE TABLE IF NOT EXISTS historial_mensual (
    mes TEXT PRIMARY KEY,
    ranking TEXT,
    total_shulker INTEGER,
    creado_en TEXT
)
""")

db.commit()

cooldowns = {}

# ===============================
# UTILIDADES
# ===============================
def format_number(num: int) -> str:
    if num >= 1_000_000:
        return f"{num // 1_000_000}M"
    if num >= 1_000:
        return f"{num // 1_000}K"
    return str(num)

def nombre_mes(fecha: date):
    meses = [
        "Enero","Febrero","Marzo","Abril","Mayo","Junio",
        "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"
    ]
    return f"{fecha.day} de {meses[fecha.month - 1]} del {fecha.year}"

# ===============================
# EMBED HISTÓRICO MENSUAL
# ===============================
async def crear_embed_historial(mes, ranking, total_shulker):
    stacks = total_shulker * 27
    bloques = stacks * 64
    niveles = bloques * 9

    descripcion = ""
    for i, (user, total) in enumerate(ranking, start=1):
        medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
        descripcion += f"{medalla} **{user}** — `{format_number(total)}` shulker\n"

    embed = discord.Embed(
        title=f"🏆 Historial Mensual • {mes}",
        description=descripcion or "_Sin datos_",
        color=discord.Color.dark_gold()
    )

    embed.add_field(name="📦 Total Shulkers", value=f"`{format_number(total_shulker)}`", inline=True)
    embed.add_field(name="🧱 Bloques End", value=f"`{format_number(bloques)}`", inline=True)
    embed.add_field(name="📈 Niveles Isla", value=f"`{format_number(niveles)}`", inline=True)

    embed.set_footer(text="Mes cerrado automáticamente")
    return embed

# ===============================
# CIERRE MENSUAL AUTOMÁTICO
# ===============================
async def cerrar_meses_faltantes():
    hoy = date.today()
    cursor.execute("SELECT DISTINCT substr(fecha, 1, 7) FROM shulker")
    meses = [m[0] for m in cursor.fetchall()]

    for mes in meses:
        cursor.execute("SELECT 1 FROM historial_mensual WHERE mes = ?", (mes,))
        if cursor.fetchone():
            continue

        inicio = date.fromisoformat(mes + "-01")
        fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        cursor.execute("""
            SELECT username, SUM(total)
            FROM shulker
            WHERE fecha BETWEEN ? AND ?
            GROUP BY user_id
            ORDER BY SUM(total) DESC
        """, (str(inicio), str(fin)))

        ranking = cursor.fetchall()
        total_mes = sum(r[1] for r in ranking)

        cursor.execute("""
            INSERT INTO historial_mensual VALUES (?, ?, ?, ?)
        """, (
            mes,
            json.dumps(ranking),
            total_mes,
            str(date.today())
        ))
        db.commit()

# ===============================
# ACTUALIZAR RANKINGS
# ===============================
async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    await cerrar_meses_faltantes()

    hoy = date.today()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

    cursor.execute("""
    SELECT * FROM historial_mensual
    WHERE total_shulker > 0
    ORDER BY mes DESC
    LIMIT 1
""")
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

    cursor.execute("SELECT * FROM historial_mensual ORDER BY mes DESC LIMIT 1")
    hist = cursor.fetchone()

    embeds = []

    if hist:
        ranking = json.loads(hist[1])
        embeds.append(await crear_embed_historial(hist[0], ranking, hist[2]))

    def embed_top(titulo, emoji, color, datos):
        desc = ""
        for i, (u, t) in enumerate(datos, 1):
            medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            desc += f"{medalla} **{u}** — `{format_number(t)}`\n"
        return discord.Embed(
            title=f"{emoji} {titulo}",
            description=desc or "_Sin registros_",
            color=color
        )

    embeds += [
        embed_top("TOP MENSUAL ACTUAL", "👑", discord.Color.purple(), mensual),
        embed_top("TOP SEMANAL", "📈", discord.Color.blue(), semanal),
        embed_top("TOP DIARIO", "⚡", discord.Color.gold(), diario)
    ]

    mensajes = []
    async for msg in channel.history(limit=10, oldest_first=True):
        if msg.author == bot.user:
            mensajes.append(msg)

    for i, embed in enumerate(embeds):
        if i < len(mensajes):
            await mensajes[i].edit(embed=embed)
        else:
            await channel.send(embed=embed)

# ===============================
# TASK
# ===============================
@tasks.loop(hours=1)
async def ranking_automatico():
    await actualizar_todos_los_ranking()

# ===============================
# MODAL
# ===============================
class ShulkerModal(discord.ui.Modal, title="Registro de Shulker"):
    cantidad = discord.ui.TextInput(label="¿Cuántas shulker colocaste?")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        ahora = time.time()

        if user_id in cooldowns and ahora - cooldowns[user_id] < COOLDOWN_SECONDS:
            return await interaction.response.send_message("⏳ Espera antes de registrar.", ephemeral=True)

        cooldowns[user_id] = ahora

        try:
            cantidad = int(self.cantidad.value)
            if cantidad <= 0:
                raise ValueError
        except:
            return await interaction.response.send_message("❌ Número inválido.", ephemeral=True)

        hoy = str(date.today())
        username = interaction.user.display_name

        cursor.execute("SELECT total FROM shulker WHERE user_id = ? AND fecha = ?", (user_id, hoy))
        row = cursor.fetchone()
        nuevo_total = cantidad if not row else row[0] + cantidad

        cursor.execute(
            "REPLACE INTO shulker VALUES (?, ?, ?, ?)",
            (user_id, username, hoy, nuevo_total)
        )
        db.commit()

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

bot.run(TOKEN)

