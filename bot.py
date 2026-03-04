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
# RUTA DB (PERSISTENTE EN RAILWAY)
# ===============================
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "shulker.db")
OLD_DB_PATH = "shulker.db"

# ===============================
# MIGRAR ARCHIVO DB VIEJO A /data (UNA VEZ)
# ===============================
os.makedirs(DATA_DIR, exist_ok=True)

if os.path.exists(OLD_DB_PATH) and not os.path.exists(DB_PATH):
    print("⚡ Migrando shulker.db viejo → /data/shulker.db")
    shutil.copy2(OLD_DB_PATH, DB_PATH)
    print("✅ Migración completada")
else:
    if os.path.exists(DB_PATH):
        print("✅ Usando base de datos persistente en /data/shulker.db")
    else:
        print("🆕 Creando nueva base de datos en /data/shulker.db")

# ===============================
# ABRIR DB
# ===============================
db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL;")
db.execute("PRAGMA synchronous=NORMAL;")
cursor = db.cursor()

# ===============================
# CONFIG DEL BOT (para "empezar desde este mes")
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
db.commit()

def get_bot_start_date() -> date:
    cursor.execute("SELECT value FROM bot_config WHERE key = 'start_date'")
    row = cursor.fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0])

    start = date.today().replace(day=1)
    cursor.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES ('start_date', ?)",
        (str(start),)
    )
    db.commit()
    print(f"📌 Start date fijada: {start} (se ignorará todo antes de esa fecha)")
    return start

BOT_START_DATE = get_bot_start_date()

# ===============================
# MIGRACIÓN DE TABLA (AGREGA PRIMARY KEY SIN PERDER DATOS)
# ===============================
def asegurar_tabla_shulker_con_pk():
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shulker'")
    existe = cursor.fetchone() is not None

    if not existe:
        cursor.execute("""
        CREATE TABLE shulker (
            user_id INTEGER,
            username TEXT,
            fecha TEXT,
            total INTEGER,
            PRIMARY KEY (user_id, fecha)
        )
        """)
        db.commit()
        print("✅ Tabla shulker creada con PRIMARY KEY (user_id, fecha)")
        return

    cursor.execute("PRAGMA table_info(shulker)")
    cols = cursor.fetchall()
    pk_cols = [c[1] for c in cols if c[5] > 0]

    if set(pk_cols) == {"user_id", "fecha"}:
        print("✅ Tabla shulker ya tiene PRIMARY KEY (user_id, fecha)")
        return

    print("⚠️ Tabla shulker sin PRIMARY KEY correcto. Migrando sin perder datos...")

    cursor.execute("ALTER TABLE shulker RENAME TO shulker_old")

    cursor.execute("""
    CREATE TABLE shulker (
        user_id INTEGER,
        username TEXT,
        fecha TEXT,
        total INTEGER,
        PRIMARY KEY (user_id, fecha)
    )
    """)

    cursor.execute("""
    INSERT INTO shulker (user_id, username, fecha, total)
    SELECT user_id, MAX(username) as username, fecha, SUM(total) as total
    FROM shulker_old
    GROUP BY user_id, fecha
    """)

    cursor.execute("DROP TABLE shulker_old")
    db.commit()
    print("✅ Migración completada: shulker ahora tiene PRIMARY KEY y datos consolidados")

asegurar_tabla_shulker_con_pk()

# ===============================
# TABLA MENSAJES FIJOS (botón fijo)
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS mensajes_fijos (
    tipo TEXT PRIMARY KEY,
    message_id INTEGER
)
""")
db.commit()

cooldowns = {}

# ===============================
# UTILIDADES (K/M + equivalencias)
# ===============================
def format_number(num: int) -> str:
    num = int(num or 0)
    if num >= 1_000_000:
        v = num / 1_000_000
        return f"{v:.1f}M" if v < 10 else f"{v:.0f}M"
    if num >= 1_000:
        v = num / 1_000
        return f"{v:.1f}K" if v < 10 else f"{v:.0f}K"
    return str(num)

def equivalencias(total_shulkers: int):
    stacks = total_shulkers * 27
    bloques = stacks * 64
    niveles = bloques * 9
    pv = total_shulkers // 27
    resto = total_shulkers % 27
    return stacks, bloques, niveles, pv, resto

def clamp_start(d: date) -> date:
    return d if d >= BOT_START_DATE else BOT_START_DATE

def total_periodo(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) FROM shulker WHERE {where_sql}", params)
    return int(cursor.fetchone()[0] or 0)

# ===============================
# MENSAJE FIJO (NO SE REPITE)
# ===============================
async def obtener_mensaje_fijo(channel: discord.TextChannel, tipo: str):
    cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    row = cursor.fetchone()

    if row:
        msg_id = row[0]
        try:
            return await channel.fetch_message(msg_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    msg = await channel.send("Cargando...")
    cursor.execute("REPLACE INTO mensajes_fijos (tipo, message_id) VALUES (?, ?)", (tipo, msg.id))
    db.commit()
    return msg

# ===============================
# CREAR EMBED RANKING (MINIMAL)
# ===============================
async def crear_embed_ranking(titulo, emoji, color, datos, footer, total_periodo_shulkers: int):
    # Ranking limpio
    if not datos:
        ranking_text = "_Sin registros aún_"
    else:
        lines = []
        for i, (user, total) in enumerate(datos, start=1):
            if i == 1:
                medalla = "🥇"
            elif i == 2:
                medalla = "🥈"
            elif i == 3:
                medalla = "🥉"
            else:
                medalla = "▫️"
            lines.append(f"{medalla} **{user}** — `{format_number(total)}`")
        ranking_text = "\n".join(lines)

    stacks, bloques, niveles, pv, resto = equivalencias(total_periodo_shulkers)

    # Resumen en 1 línea
    resumen = (
        f"`{format_number(total_periodo_shulkers)}` shulkers • "
        f"`{format_number(stacks)}` stacks • "
        f"`{format_number(bloques)}` bloques • "
        f"`{format_number(niveles)}` niveles • "
        f"`{pv}` PV"
    )

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=ranking_text,
        color=color
    )

    embed.add_field(name="📊 Resumen", value=resumen, inline=False)
    embed.add_field(name="📦 Total", value=f"`{format_number(total_periodo_shulkers)}` shulkers", inline=True)
    embed.add_field(name="⚖ Equivalente", value=f"`{pv}` PV + `{resto}` shulkers", inline=True)

    embed.set_footer(text=footer)
    return embed

# ===============================
# ACTUALIZAR RANKINGS (EDITA, NO SPAM)
# ===============================
async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    hoy = date.today()
    inicio_mes = clamp_start(hoy.replace(day=1))
    inicio_semana = clamp_start(hoy - timedelta(days=hoy.weekday()))

    # TOP 5 (minimal)
    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 5
    """, (str(inicio_mes),))
    mensual = cursor.fetchall()
    total_mensual = total_periodo("fecha >= ?", (str(inicio_mes),))

    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 5
    """, (str(inicio_semana),))
    semanal = cursor.fetchall()
    total_semanal = total_periodo("fecha >= ?", (str(inicio_semana),))

    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha = ? AND fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 5
    """, (str(hoy), str(BOT_START_DATE)))
    diario = cursor.fetchall()
    total_diario = total_periodo("fecha = ? AND fecha >= ?", (str(hoy), str(BOT_START_DATE)))

    embeds = [
        await crear_embed_ranking(
            "TOP MENSUAL", "👑", discord.Color.purple(),
            mensual, "🗓️ Mes actual • desde el inicio del bot", total_mensual
        ),
        await crear_embed_ranking(
            "TOP SEMANAL", "📈", discord.Color.blue(),
            semanal, f"📅 Semana actual • desde {inicio_semana}", total_semanal
        ),
        await crear_embed_ranking(
            "TOP DIARIO", "⚡", discord.Color.gold(),
            diario, f"📆 Hoy • {hoy}", total_diario
        )
    ]

    mensajes = []
    async for msg in channel.history(limit=6, oldest_first=True):
        if msg.author == bot.user and msg.embeds:
            mensajes.append(msg)

    for i, embed in enumerate(embeds):
        if i < len(mensajes):
            await mensajes[i].edit(embed=embed)
        else:
            await channel.send(embed=embed)

# ===============================
# TASK AUTOMÁTICO
# ===============================
@tasks.loop(hours=1)
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

        cursor.execute("""
            INSERT INTO shulker (user_id, username, fecha, total)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, fecha)
            DO UPDATE SET
                total = total + excluded.total,
                username = excluded.username
        """, (user_id, username, hoy, cantidad_int))
        db.commit()

        cursor.execute("SELECT total FROM shulker WHERE user_id = ? AND fecha = ?", (user_id, hoy))
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
    print(f"📂 Base de datos activa: {DB_PATH}")
    print(f"📌 Start date: {BOT_START_DATE} (se ignora todo antes)")

    if os.path.exists(DB_PATH):
        size_kb = os.path.getsize(DB_PATH) / 1024
        print(f"   Tamaño actual: {size_kb:.2f} KB")

    if not ranking_automatico.is_running():
        ranking_automatico.start()

    # Mensaje fijo del botón (no se repite)
    channel = bot.get_channel(FORM_CHANNEL_ID)
    if channel:
        msg = await obtener_mensaje_fijo(channel, "form_boton")
        await msg.edit(
            content=None,
            embed=discord.Embed(
                title="🧰 Registro de Shulker",
                description="Presiona el botón para registrar.",
                color=discord.Color.green()
            ),
            view=ShulkerButton()
        )

    await actualizar_todos_los_ranking()

# ===============================
# INICIAR BOT
# ===============================
bot.run(TOKEN)
