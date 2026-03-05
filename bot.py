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

# Canal privado del progreso
PROGRESS_CHANNEL_ID = 1478948711995412702

TOKEN = os.getenv("DISCORD_TOKEN")
COOLDOWN_SECONDS = 60

# Metas (ajústalas cuando quieras)
TARGET_TOP1_LEVEL = 105_000_000  # ✅ tu competencia TOP 1
TARGET_TOP3_LEVEL = 80_000_000   # opcional, ajusta si quieres
DAILY_SHULKER_GOAL = 120

# ===============================
# CONVERSIÓN EXACTA SEGÚN TU SERVER
# ===============================
LEVELS_PER_BLOCK = 9                       # 1 bloque = 9 niveles
LEVELS_PER_STACK = 64 * LEVELS_PER_BLOCK   # 576 niveles
LEVELS_PER_SHULKER = 27 * LEVELS_PER_STACK # 15552 niveles ✅
SHULKERS_PER_PV = 27
LEVELS_PER_PV = SHULKERS_PER_PV * LEVELS_PER_SHULKER  # 419,904 ✅

# Nivel exacto base (se usará solo si no hay base guardada aún)
DEFAULT_BASE_LEVEL = 42127075

# ===============================
# DB PERSISTENTE (RAILWAY)
# ===============================
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "shulker.db")
OLD_DB_PATH = "shulker.db"

os.makedirs(DATA_DIR, exist_ok=True)

# Migración vieja → /data (una vez)
if os.path.exists(OLD_DB_PATH) and not os.path.exists(DB_PATH):
    print("⚡ Migrando shulker.db viejo → /data/shulker.db")
    shutil.copy2(OLD_DB_PATH, DB_PATH)
    print("✅ Migración completada")

db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL;")
db.execute("PRAGMA synchronous=NORMAL;")
cursor = db.cursor()

# ===============================
# TABLAS
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS mensajes_fijos (
    tipo TEXT PRIMARY KEY,
    message_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS island_progress (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

db.commit()

# ===============================
# START DATE (empezar desde este mes)
# ===============================
def get_bot_start_date() -> date:
    cursor.execute("SELECT value FROM bot_config WHERE key='start_date'")
    row = cursor.fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0])

    start = date.today().replace(day=1)
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('start_date', ?)", (str(start),))
    db.commit()
    return start

BOT_START_DATE = get_bot_start_date()

def clamp_start(d: date) -> date:
    return d if d >= BOT_START_DATE else BOT_START_DATE

# ===============================
# MIGRACIÓN: asegurar PRIMARY KEY (user_id, fecha)
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
        print("✅ Tabla shulker creada con PRIMARY KEY")
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
    print("✅ Migración completada: shulker ahora tiene PRIMARY KEY")

asegurar_tabla_shulker_con_pk()

# ===============================
# COOLDOWN
# ===============================
cooldowns = {}

# ===============================
# UTILIDADES
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

def barra_progreso(valor: int, maximo: int, largo: int = 10) -> str:
    if maximo <= 0:
        return "░" * largo
    ratio = valor / maximo
    llenos = int(round(ratio * largo))
    llenos = max(0, min(largo, llenos))
    return "█" * llenos + "░" * (largo - llenos)

def equivalencias(shulkers: int):
    # ✅ exacto según tus reglas
    stacks = shulkers * 27
    niveles = shulkers * LEVELS_PER_SHULKER
    pv = shulkers // SHULKERS_PER_PV
    resto = shulkers % SHULKERS_PER_PV
    return stacks, niveles, pv, resto

def shulkers_a_pv_y_shulkers(shulkers: int):
    pv = shulkers // SHULKERS_PER_PV
    resto = shulkers % SHULKERS_PER_PV
    return pv, resto

def get_progress_value(key: str, default: str = "") -> str:
    cursor.execute("SELECT value FROM island_progress WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row and row[0] is not None else default

def set_progress_value(key: str, value: str):
    cursor.execute("INSERT OR REPLACE INTO island_progress (key, value) VALUES (?, ?)", (key, value))
    db.commit()

def total_shulkers_all_time() -> int:
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM shulker")
    return int(cursor.fetchone()[0] or 0)

def total_shulkers_today() -> int:
    hoy = str(date.today())
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM shulker WHERE fecha = ?", (hoy,))
    return int(cursor.fetchone()[0] or 0)

def barra_meta(valor: int, meta: int, largo: int = 16) -> str:
    if meta <= 0:
        return "░" * largo
    ratio = max(0.0, min(1.0, valor / meta))
    llenos = int(round(ratio * largo))
    return "█" * llenos + "░" * (largo - llenos)

async def obtener_mensaje_fijo(channel: discord.TextChannel, tipo: str):
    cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    row = cursor.fetchone()

    if row:
        try:
            return await channel.fetch_message(row[0])
        except:
            pass

    msg = await channel.send("Cargando...")
    cursor.execute("INSERT OR REPLACE INTO mensajes_fijos (tipo, message_id) VALUES (?, ?)", (tipo, msg.id))
    db.commit()
    return msg

# ===============================
# PANEL PRIVADO PROGRESO ISLA
# ===============================
def asegurar_base_progreso_si_falta():
    base_level = get_progress_value("base_level", "")
    if base_level:
        return

    set_progress_value("base_level", str(DEFAULT_BASE_LEVEL))
    set_progress_value("base_shulkers", str(total_shulkers_all_time()))
    set_progress_value("base_date", str(date.today()))
    print(f"✅ Base progreso creada automáticamente: {DEFAULT_BASE_LEVEL:,}")

async def actualizar_panel_progreso():
    if not PROGRESS_CHANNEL_ID:
        return

    channel = bot.get_channel(PROGRESS_CHANNEL_ID)
    if not channel:
        return

    base_level = int(get_progress_value("base_level", "0") or 0)
    base_shulkers = int(get_progress_value("base_shulkers", "0") or 0)
    base_date = get_progress_value("base_date", "")

    total_sh = total_shulkers_all_time()
    hoy_sh = total_shulkers_today()

    nuevos_sh = max(0, total_sh - base_shulkers)
    niveles_ganados = nuevos_sh * LEVELS_PER_SHULKER
    nivel_estimado = base_level + niveles_ganados

    # faltantes en niveles
    faltan_top1 = max(0, TARGET_TOP1_LEVEL - nivel_estimado)
    faltan_top3 = max(0, TARGET_TOP3_LEVEL - nivel_estimado)

    # Convertir faltantes a shulkers (ceil)
    shulkers_top1 = (faltan_top1 + LEVELS_PER_SHULKER - 1) // LEVELS_PER_SHULKER if LEVELS_PER_SHULKER > 0 else 0
    shulkers_top3 = (faltan_top3 + LEVELS_PER_SHULKER - 1) // LEVELS_PER_SHULKER if LEVELS_PER_SHULKER > 0 else 0

    # Mostrar como PVS + SHULKERS
    pv1, sh1 = shulkers_a_pv_y_shulkers(shulkers_top1)
    pv3, sh3 = shulkers_a_pv_y_shulkers(shulkers_top3)

    faltan_diario = max(0, DAILY_SHULKER_GOAL - hoy_sh)

    bar_top1 = barra_meta(nivel_estimado, TARGET_TOP1_LEVEL, largo=16)
    bar_top3 = barra_meta(nivel_estimado, TARGET_TOP3_LEVEL, largo=16)

    embed = discord.Embed(
        title="🏝️ PROGRESO DE LA ISLA (PRIVADO)",
        description=(
            f"**NIVEL ACTUAL (estimado):** `{nivel_estimado:,}`\n"
            f"**SHULKERS HOY:** `{hoy_sh:,}` / `{DAILY_SHULKER_GOAL:,}`  (FALTAN `{faltan_diario:,}`)\n"
        ),
        color=discord.Color.dark_teal()
    )

    embed.add_field(
        name="🎯 META TOP 1",
        value=(
            f"`{nivel_estimado:,}` / `{TARGET_TOP1_LEVEL:,}`\n"
            f"`{bar_top1}`\n"
            f"FALTAN: `{faltan_top1:,}` niveles ≈ `{pv1}` PVS + `{sh1}` SHULKERS"
        ),
        inline=False
    )

    embed.add_field(
        name="📈 META TOP 3",
        value=(
            f"`{nivel_estimado:,}` / `{TARGET_TOP3_LEVEL:,}`\n"
            f"`{bar_top3}`\n"
            f"FALTAN: `{faltan_top3:,}` niveles ≈ `{pv3}` PVS + `{sh3}` SHULKERS"
        ),
        inline=False
    )

    embed.set_footer(text=f"Base exacta: {base_level:,} | Desde: {base_date or 'sin calibrar'}")

    msg = await obtener_mensaje_fijo(channel, "panel_progreso")
    await msg.edit(content=None, embed=embed)

# Recalibrar cuando tú quieras (admin)
@bot.command(name="setnivel")
@commands.has_permissions(administrator=True)
async def setnivel(ctx, nivel: int):
    set_progress_value("base_level", str(nivel))
    set_progress_value("base_shulkers", str(total_shulkers_all_time()))
    set_progress_value("base_date", str(date.today()))
    await ctx.reply(f"✅ Nivel base fijado en `{nivel:,}` (recalibrado).", mention_author=False)
    await actualizar_panel_progreso()

# ===============================
# EMBED RANKING (TOP 5 + barra)
# ===============================
async def crear_embed_ranking(titulo, emoji, color, datos, footer, total_periodo_shulkers: int):
    if not datos:
        ranking_text = "_Sin registros aún_"
        maximo = 0
    else:
        maximo = int(datos[0][1] or 0)
        lines = []
        for i, (user, total) in enumerate(datos, start=1):
            total = int(total or 0)
            medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
            bar = barra_progreso(total, maximo, largo=10)
            lines.append(f"{medalla} **{user}** — `{format_number(total)}` `{bar}`")
        ranking_text = "\n".join(lines)

    _, niveles, pv, resto = equivalencias(total_periodo_shulkers)

    resumen = (
        f"`{format_number(total_periodo_shulkers)}` SHULKERS • "
        f"`{format_number(niveles)}` NIVELES • "
        f"`{pv}` PVS"
    )

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=ranking_text,
        color=color
    )
    embed.add_field(name="📊 RESUMEN", value=resumen, inline=False)
    embed.add_field(name="📦 TOTAL", value=f"`{format_number(total_periodo_shulkers)}` SHULKERS", inline=True)
    embed.add_field(name="⚖ EQUIVALENTE", value=f"`{pv}` PVS + `{resto}` SHULKERS", inline=True)
    embed.set_footer(text=footer)
    return embed

# ===============================
# ACTUALIZAR TOPS
# ===============================
async def total_periodo(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) FROM shulker WHERE {where_sql}", params)
    return int(cursor.fetchone()[0] or 0)

async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    hoy = date.today()
    inicio_mes = clamp_start(hoy.replace(day=1))
    inicio_semana = clamp_start(hoy - timedelta(days=hoy.weekday()))

    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 5
    """, (str(inicio_mes),))
    mensual = cursor.fetchall()
    total_mensual = await total_periodo("fecha >= ?", (str(inicio_mes),))

    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 5
    """, (str(inicio_semana),))
    semanal = cursor.fetchall()
    total_semanal = await total_periodo("fecha >= ?", (str(inicio_semana),))

    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha = ? AND fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 5
    """, (str(hoy), str(BOT_START_DATE)))
    diario = cursor.fetchall()
    total_diario = await total_periodo("fecha = ? AND fecha >= ?", (str(hoy), str(BOT_START_DATE)))

    embeds = [
        await crear_embed_ranking("TOP MENSUAL", "👑", discord.Color.purple(), mensual, "Mes actual", total_mensual),
        await crear_embed_ranking("TOP SEMANAL", "📈", discord.Color.blue(), semanal, f"Desde {inicio_semana}", total_semanal),
        await crear_embed_ranking("TOP DIARIO", "⚡", discord.Color.gold(), diario, f"Hoy • {hoy}", total_diario),
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
# TASKS
# ===============================
@tasks.loop(hours=1)
async def ranking_automatico():
    await actualizar_todos_los_ranking()
    await actualizar_panel_progreso()

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
        nuevo_total = int(cursor.fetchone()[0] or 0)

        await actualizar_todos_los_ranking()
        await actualizar_panel_progreso()

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
    print(f"📂 DB: {DB_PATH}")
    print(f"📌 Start date: {BOT_START_DATE}")
    print(f"✅ LEVELS_PER_SHULKER: {LEVELS_PER_SHULKER} | LEVELS_PER_PV: {LEVELS_PER_PV}")

    asegurar_base_progreso_si_falta()

    if not ranking_automatico.is_running():
        ranking_automatico.start()

    # Mensaje fijo del botón (no se duplica)
    form_channel = bot.get_channel(FORM_CHANNEL_ID)
    if form_channel:
        msg = await obtener_mensaje_fijo(form_channel, "form_boton")
        await msg.edit(
            content=None,
            embed=discord.Embed(
                title="📦 Registro de Shulker",
                description="Presiona el botón para registrar.",
                color=discord.Color.green()
            ),
            view=ShulkerButton()
        )

    await actualizar_todos_los_ranking()
    await actualizar_panel_progreso()

# ===============================
# RUN
# ===============================
bot.run(TOKEN)

