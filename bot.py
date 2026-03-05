import os
import time
import sqlite3
import discord
import shutil
from datetime import date, timedelta
from discord.ext import commands, tasks

# ===============================
# BOT
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ===============================
# CONFIGURACIÓN
# ===============================
FORM_CHANNEL_ID = 1465764092978532547
RANKING_CHANNEL_ID = 1468791225619320894
END_CHANNEL_ID = 1462316362515873947

TOKEN = os.getenv("DISCORD_TOKEN")
COOLDOWN_SECONDS = 60

# ===============================
# BASE DE DATOS PERSISTENTE
# ===============================
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "shulker.db")
OLD_DB_PATH = "shulker.db"

os.makedirs(DATA_DIR, exist_ok=True)

if os.path.exists(OLD_DB_PATH) and not os.path.exists(DB_PATH):
    shutil.copy2(OLD_DB_PATH, DB_PATH)

db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
cursor = db.cursor()

# ===============================
# TABLA SHULKER
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker(
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, fecha)
)
""")

# ===============================
# TABLA CONFIG BOT
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_config(
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# ===============================
# TABLA MENSAJE FIJO
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS mensajes_fijos(
    tipo TEXT PRIMARY KEY,
    message_id INTEGER
)
""")

db.commit()

# ===============================
# START DATE (inicio del bot)
# ===============================
def get_bot_start_date():
    cursor.execute("SELECT value FROM bot_config WHERE key='start_date'")
    row = cursor.fetchone()

    if row:
        return date.fromisoformat(row[0])

    start = date.today().replace(day=1)

    cursor.execute(
        "INSERT OR REPLACE INTO bot_config VALUES('start_date', ?)",
        (str(start),)
    )

    db.commit()
    return start


BOT_START_DATE = get_bot_start_date()

cooldowns = {}

# ===============================
# FORMATEO NUMEROS
# ===============================
def format_number(num):

    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"

    if num >= 1_000:
        return f"{num/1_000:.1f}K"

    return str(num)

# ===============================
# BARRA PROGRESO (10 bloques)
# ===============================
def barra_progreso(valor, maximo, largo=10):

    if maximo <= 0:
        return "░" * largo

    ratio = valor / maximo
    llenos = int(round(ratio * largo))

    return "█" * llenos + "░" * (largo - llenos)

# ===============================
# EQUIVALENCIAS
# ===============================
def equivalencias(shulkers):

    stacks = shulkers * 27
    bloques = stacks * 64
    niveles = bloques * 9

    pv = shulkers // 27
    resto = shulkers % 27

    return stacks, bloques, niveles, pv, resto

# ===============================
# EMBED RANKING
# ===============================
async def crear_embed_ranking(
        titulo,
        emoji,
        color,
        datos,
        footer,
        total_periodo):

    if not datos:
        ranking = "_Sin registros_"
        maximo = 0

    else:

        maximo = datos[0][1]

        lineas = []

        for i, (user, total) in enumerate(datos, start=1):

            if i == 1:
                medalla = "🥇"

            elif i == 2:
                medalla = "🥈"

            elif i == 3:
                medalla = "🥉"

            else:
                medalla = "▫️"

            barra = barra_progreso(total, maximo)

            lineas.append(
                f"{medalla} **{user}** — `{format_number(total)}` `{barra}`"
            )

        ranking = "\n".join(lineas)

    _, _, niveles, pv, resto = equivalencias(total_periodo)

    resumen = (
        f"`{format_number(total_periodo)}` SHULKERS • "
        f"`{format_number(niveles)}` NIVELES • "
        f"`{pv}` PVS"
    )

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=ranking,
        color=color
    )

    embed.add_field(name="📊 RESUMEN", value=resumen, inline=False)

    embed.add_field(
        name="📦 TOTAL",
        value=f"`{format_number(total_periodo)}` SHULKERS",
        inline=True
    )

    embed.add_field(
        name="⚖ EQUIVALENTE",
        value=f"`{pv}` PVS + `{resto}` SHULKERS",
        inline=True
    )

    embed.set_footer(text=footer)

    return embed

# ===============================
# ACTUALIZAR RANKING
# ===============================
async def actualizar_todos_los_ranking():

    channel = bot.get_channel(RANKING_CHANNEL_ID)

    if not channel:
        return

    hoy = date.today()
    inicio_mes = hoy.replace(day=1)
    inicio_semana = hoy - timedelta(days=hoy.weekday())

    cursor.execute("""
    SELECT username, SUM(total)
    FROM shulker
    WHERE fecha >= ?
    GROUP BY user_id
    ORDER BY SUM(total) DESC
    LIMIT 5
    """, (str(inicio_mes),))

    mensual = cursor.fetchall()

    cursor.execute("""
    SELECT SUM(total)
    FROM shulker
    WHERE fecha >= ?
    """, (str(inicio_mes),))

    total_mensual = cursor.fetchone()[0] or 0

    cursor.execute("""
    SELECT username, SUM(total)
    FROM shulker
    WHERE fecha >= ?
    GROUP BY user_id
    ORDER BY SUM(total) DESC
    LIMIT 5
    """, (str(inicio_semana),))

    semanal = cursor.fetchall()

    cursor.execute("""
    SELECT SUM(total)
    FROM shulker
    WHERE fecha >= ?
    """, (str(inicio_semana),))

    total_semanal = cursor.fetchone()[0] or 0

    cursor.execute("""
    SELECT username, SUM(total)
    FROM shulker
    WHERE fecha = ?
    GROUP BY user_id
    ORDER BY SUM(total) DESC
    LIMIT 5
    """, (str(hoy),))

    diario = cursor.fetchall()

    cursor.execute("""
    SELECT SUM(total)
    FROM shulker
    WHERE fecha = ?
    """, (str(hoy),))

    total_diario = cursor.fetchone()[0] or 0

    embeds = [

        await crear_embed_ranking(
            "TOP MENSUAL",
            "👑",
            discord.Color.purple(),
            mensual,
            "Mes actual",
            total_mensual
        ),

        await crear_embed_ranking(
            "TOP SEMANAL",
            "📈",
            discord.Color.blue(),
            semanal,
            f"Desde {inicio_semana}",
            total_semanal
        ),

        await crear_embed_ranking(
            "TOP DIARIO",
            "⚡",
            discord.Color.gold(),
            diario,
            f"Hoy • {hoy}",
            total_diario
        )
    ]

    mensajes = []

    async for msg in channel.history(limit=5, oldest_first=True):

        if msg.author == bot.user and msg.embeds:
            mensajes.append(msg)

    for i, embed in enumerate(embeds):

        if i < len(mensajes):

            await mensajes[i].edit(embed=embed)

        else:

            await channel.send(embed=embed)

# ===============================
# LOOP AUTOMATICO
# ===============================
@tasks.loop(hours=1)
async def ranking_automatico():

    await actualizar_todos_los_ranking()

# ===============================
# MODAL
# ===============================
class ShulkerModal(discord.ui.Modal, title="Registro de Shulker"):

    cantidad = discord.ui.TextInput(
        label="¿Cuántas shulker colocaste?"
    )

    async def on_submit(self, interaction: discord.Interaction):

        user_id = interaction.user.id
        ahora = time.time()

        if user_id in cooldowns and ahora - cooldowns[user_id] < COOLDOWN_SECONDS:

            await interaction.response.send_message(
                "⏳ Espera antes de registrar.",
                ephemeral=True
            )

            return

        cooldowns[user_id] = ahora

        try:
            cantidad_int = int(self.cantidad.value)

        except:

            await interaction.response.send_message(
                "❌ Número inválido.",
                ephemeral=True
            )

            return

        hoy = str(date.today())
        username = interaction.user.display_name

        cursor.execute("""
        INSERT INTO shulker(user_id, username, fecha, total)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id, fecha)
        DO UPDATE SET
            total = total + excluded.total,
            username = excluded.username
        """, (user_id, username, hoy, cantidad_int))

        db.commit()

        cursor.execute(
            "SELECT total FROM shulker WHERE user_id=? AND fecha=?",
            (user_id, hoy)
        )

        nuevo_total = cursor.fetchone()[0]

        await actualizar_todos_los_ranking()

        end_channel = interaction.client.get_channel(END_CHANNEL_ID)

        if end_channel:

            embed = discord.Embed(
                title="📦 Registro de Shulker",
                description=(
                    f"👤 {interaction.user.mention}\n"
                    f"➕ `{cantidad_int}`\n"
                    f"📊 Total hoy: `{nuevo_total}`"
                ),
                color=discord.Color.green()
            )

            await end_channel.send(embed=embed)

        await interaction.response.send_message(
            "✅ Registro guardado.",
            ephemeral=True
        )

# ===============================
# BOTON
# ===============================
class ShulkerButton(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar Shulker",
        style=discord.ButtonStyle.green,
        emoji="📦"
    )
    async def registrar(self, interaction, button):

        await interaction.response.send_modal(ShulkerModal())

# ===============================
# READY
# ===============================
@bot.event
async def on_ready():

    print(f"Bot conectado como {bot.user}")

    if not ranking_automatico.is_running():
        ranking_automatico.start()

    channel = bot.get_channel(FORM_CHANNEL_ID)

    if channel:

        cursor.execute(
            "SELECT message_id FROM mensajes_fijos WHERE tipo='form'"
        )

        row = cursor.fetchone()

        if row:

            try:

                msg = await channel.fetch_message(row[0])

                await msg.edit(
                    embed=discord.Embed(
                        title="📦 Registro de Shulker",
                        description="Presiona el botón para registrar.",
                        color=discord.Color.green()
                    ),
                    view=ShulkerButton()
                )

                return

            except:
                pass

        msg = await channel.send(
            embed=discord.Embed(
                title="📦 Registro de Shulker",
                description="Presiona el botón para registrar.",
                color=discord.Color.green()
            ),
            view=ShulkerButton()
        )

        cursor.execute(
            "INSERT OR REPLACE INTO mensajes_fijos VALUES('form',?)",
            (msg.id,)
        )

        db.commit()

# ===============================
# RUN BOT
# ===============================
bot.run(TOKEN)
