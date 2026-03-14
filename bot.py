import os
import time
import sqlite3
import discord
import shutil
from datetime import date, timedelta, datetime
from discord.ext import commands, tasks

# ===============================
# CONFIGURACIÓN
# ===============================
FORM_CHANNEL_ID = 1465764092978532547
RANKING_CHANNEL_ID = 1468791225619320894
END_CHANNEL_ID = 1462316362515873947
PROGRESS_CHANNEL_ID = 1478948711995412702

# ===============================
# NUEVO SISTEMA END APORTADA
# ===============================
END_APORTE_FORM_CHANNEL_ID = 1482278081552187435         # <-- PON AQUÍ EL ID DEL CANAL DEL BOTÓN + SUBIDA DE IMAGEN
END_APORTE_RANKING_CHANNEL_ID = 1482278329871503461      # <-- PON AQUÍ EL ID DEL CANAL DE TOPS PÚBLICOS
END_APORTE_REVIEW_CHANNEL_ID = 1482278518552530955       # <-- PON AQUÍ EL ID DEL CANAL PRIVADO DE STAFF

TOKEN = os.getenv("DISCORD_TOKEN")
COOLDOWN_SECONDS = 60
END_UPLOAD_TIMEOUT_SECONDS = 120

TARGET_TOP1_LEVEL = 105_000_000
TARGET_TOP3_LEVEL = 80_000_000
DAILY_SHULKER_GOAL = 120

# ===============================
# CONVERSIÓN EXACTA SEGÚN TU SERVER
# ===============================
LEVELS_PER_BLOCK = 9
LEVELS_PER_STACK = 64 * LEVELS_PER_BLOCK          # 576
LEVELS_PER_SHULKER = 27 * LEVELS_PER_STACK        # 15552
SHULKERS_PER_PV = 27
LEVELS_PER_PV = SHULKERS_PER_PV * LEVELS_PER_SHULKER  # 419904

DEFAULT_BASE_LEVEL = 42127075

# ===============================
# DB PERSISTENTE (RAILWAY)
# ===============================
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "shulker.db")
OLD_DB_PATH = "shulker.db"

os.makedirs(DATA_DIR, exist_ok=True)

if os.path.exists(OLD_DB_PATH) and not os.path.exists(DB_PATH):
    print("⚡ Migrando shulker.db viejo → /data/shulker.db")
    shutil.copy2(OLD_DB_PATH, DB_PATH)
    print("✅ Migración completada")

db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL;")
db.execute("PRAGMA synchronous=NORMAL;")
cursor = db.cursor()

# ===============================
# BOT
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class ShulkerBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(ShulkerButton())
        self.add_view(EndAportadoButton())
        self.add_view(EndReviewView())
        print("✅ Vistas persistentes registradas en setup_hook")

bot = ShulkerBot(command_prefix="!", intents=intents)

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

cursor.execute("""
CREATE TABLE IF NOT EXISTS end_aportado (
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, fecha)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS end_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    fecha TEXT NOT NULL,
    cantidad INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    review_message_id INTEGER,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
)
""")

db.commit()

# ===============================
# START DATE
# ===============================
def get_bot_start_date() -> date:
    cursor.execute("SELECT value FROM bot_config WHERE key='start_date'")
    row = cursor.fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0])

    start = date.today().replace(day=1)
    cursor.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES ('start_date', ?)",
        (str(start),)
    )
    db.commit()
    return start

BOT_START_DATE = get_bot_start_date()

def clamp_start(d: date) -> date:
    return d if d >= BOT_START_DATE else BOT_START_DATE

# ===============================
# MIGRACIÓN TABLA SHULKER
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
# COOLDOWNS
# ===============================
cooldowns = {}
end_aporte_cooldowns = {}

# ===============================
# PENDIENTES DE IMAGEN
# ===============================
end_upload_pending = {}

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

def cortar_nombre(nombre: str, limite: int = 20) -> str:
    nombre = str(nombre or "")
    return nombre if len(nombre) <= limite else nombre[:limite - 1] + "…"

def barra_progreso(valor: int, maximo: int, largo: int = 12) -> str:
    if maximo <= 0:
        return "▱" * largo

    ratio = valor / maximo
    ratio = max(0.0, min(1.0, ratio))

    llenos = int(round(ratio * largo))
    llenos = max(0, min(largo, llenos))

    return "▰" * llenos + "▱" * (largo - llenos)

def barra_meta(valor: int, meta: int, largo: int = 20) -> str:
    if meta <= 0:
        return "▱" * largo

    ratio = valor / meta
    ratio = max(0.0, min(1.0, ratio))

    llenos = int(round(ratio * largo))
    llenos = max(0, min(largo, llenos))

    return "▰" * llenos + "▱" * (largo - llenos)

def equivalencias(shulkers: int):
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
    cursor.execute(
        "INSERT OR REPLACE INTO island_progress (key, value) VALUES (?, ?)",
        (key, value)
    )
    db.commit()

def total_shulkers_all_time() -> int:
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM shulker")
    return int(cursor.fetchone()[0] or 0)

def total_shulkers_today() -> int:
    hoy = str(date.today())
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM shulker WHERE fecha = ?", (hoy,))
    return int(cursor.fetchone()[0] or 0)

async def obtener_mensaje_fijo(channel: discord.TextChannel, tipo: str):
    cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    row = cursor.fetchone()

    if row:
        try:
            return await channel.fetch_message(row[0])
        except Exception as e:
            print(f"⚠️ No se pudo recuperar mensaje fijo {tipo}: {e}")

    msg = await channel.send("Cargando...")
    cursor.execute(
        "INSERT OR REPLACE INTO mensajes_fijos (tipo, message_id) VALUES (?, ?)",
        (tipo, msg.id)
    )
    db.commit()
    return msg

def now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def limpiar_pendiente_end(user_id: int):
    if user_id in end_upload_pending:
        del end_upload_pending[user_id]

def get_pending_end(user_id: int):
    data = end_upload_pending.get(user_id)
    if not data:
        return None

    if time.time() - data["timestamp"] > END_UPLOAD_TIMEOUT_SECONDS:
        limpiar_pendiente_end(user_id)
        return None

    return data

def is_staff_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_guild:
        return True
    if member.guild_permissions.manage_messages:
        return True
    return False

def get_request_by_review_message_id(message_id: int):
    cursor.execute("""
        SELECT id, user_id, username, fecha, cantidad, image_url, status,
               review_message_id, reviewed_by, reviewed_at, created_at
        FROM end_requests
        WHERE review_message_id = ?
    """, (message_id,))
    return cursor.fetchone()

def get_request_by_id(request_id: int):
    cursor.execute("""
        SELECT id, user_id, username, fecha, cantidad, image_url, status,
               review_message_id, reviewed_by, reviewed_at, created_at
        FROM end_requests
        WHERE id = ?
    """, (request_id,))
    return cursor.fetchone()

def construir_embed_revision(request_row, estado_override: str | None = None, reviewer_name: str | None = None):
    (
        request_id,
        user_id,
        username,
        fecha_registro,
        cantidad,
        image_url,
        status,
        review_message_id,
        reviewed_by,
        reviewed_at,
        created_at
    ) = request_row

    status = estado_override or status

    if status == "approved":
        color = discord.Color.green()
        estado_texto = "✅ APROBADO"
    elif status == "rejected":
        color = discord.Color.red()
        estado_texto = "❌ RECHAZADO"
    else:
        color = discord.Color.orange()
        estado_texto = "🟡 PENDIENTE"

    embed = discord.Embed(
        title="🪨 Solicitud de End aportada",
        color=color,
        timestamp=datetime.utcnow()
    )

    embed.add_field(name="👤 Usuario", value=f"<@{user_id}>", inline=True)
    embed.add_field(name="📦 Cantidad", value=f"`{cantidad}` shulkers", inline=True)
    embed.add_field(name="📅 Fecha", value=f"`{fecha_registro}`", inline=True)
    embed.add_field(name="📌 Estado", value=estado_texto, inline=False)
    embed.add_field(name="🆔 Solicitud", value=f"`#{request_id}`", inline=True)
    embed.add_field(name="🕒 Creada", value=f"`{created_at}`", inline=True)

    if reviewer_name and reviewed_at:
        embed.add_field(
            name="🛡 Revisado por",
            value=f"`{reviewer_name}`\n`{reviewed_at}`",
            inline=False
        )
    elif reviewed_by and reviewed_at:
        embed.add_field(
            name="🛡 Revisado",
            value=f"<@{reviewed_by}>\n`{reviewed_at}`",
            inline=False
        )

    embed.set_image(url=image_url)
    embed.set_footer(text=f"Solicitud #{request_id}")
    return embed

# ===============================
# PANEL PRIVADO
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
    try:
        if not PROGRESS_CHANNEL_ID:
            return

        channel = bot.get_channel(PROGRESS_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró PROGRESS_CHANNEL_ID")
            return

        base_level = int(get_progress_value("base_level", "0") or 0)
        base_shulkers = int(get_progress_value("base_shulkers", "0") or 0)
        base_date = get_progress_value("base_date", "")

        total_sh = total_shulkers_all_time()
        hoy_sh = total_shulkers_today()

        nuevos_sh = max(0, total_sh - base_shulkers)
        niveles_ganados = nuevos_sh * LEVELS_PER_SHULKER
        nivel_estimado = base_level + niveles_ganados

        faltan_top1 = max(0, TARGET_TOP1_LEVEL - nivel_estimado)
        faltan_top3 = max(0, TARGET_TOP3_LEVEL - nivel_estimado)

        shulkers_top1 = (faltan_top1 + LEVELS_PER_SHULKER - 1) // LEVELS_PER_SHULKER if LEVELS_PER_SHULKER > 0 else 0
        shulkers_top3 = (faltan_top3 + LEVELS_PER_SHULKER - 1) // LEVELS_PER_SHULKER if LEVELS_PER_SHULKER > 0 else 0

        pv1, sh1 = shulkers_a_pv_y_shulkers(shulkers_top1)
        pv3, sh3 = shulkers_a_pv_y_shulkers(shulkers_top3)

        faltan_diario = max(0, DAILY_SHULKER_GOAL - hoy_sh)

        pct_top1 = (nivel_estimado / TARGET_TOP1_LEVEL) * 100 if TARGET_TOP1_LEVEL else 0
        pct_top3 = (nivel_estimado / TARGET_TOP3_LEVEL) * 100 if TARGET_TOP3_LEVEL else 0
        pct_dia = (hoy_sh / DAILY_SHULKER_GOAL) * 100 if DAILY_SHULKER_GOAL else 0

        bar_top1 = barra_meta(nivel_estimado, TARGET_TOP1_LEVEL, largo=20)
        bar_top3 = barra_meta(nivel_estimado, TARGET_TOP3_LEVEL, largo=20)
        bar_dia = barra_meta(hoy_sh, DAILY_SHULKER_GOAL, largo=20)

        embed = discord.Embed(
            title="🏝️ PROGRESO DE LA ISLA (PANEL NUEVO)",
            color=discord.Color.dark_teal()
        )

        embed.add_field(
            name="🔹 NIVEL ACTUAL",
            value=f"**{nivel_estimado:,}**",
            inline=False
        )

        embed.add_field(
            name="📦 META DIARIA",
            value=(
                f"`{hoy_sh:,} / {DAILY_SHULKER_GOAL:,} shulkers`\n"
                f"`{bar_dia}` `{pct_dia:.1f}%`\n"
                f"Faltan: `{faltan_diario:,}`"
            ),
            inline=False
        )

        embed.add_field(
            name="👑 META TOP 1",
            value=(
                f"`{nivel_estimado:,} / {TARGET_TOP1_LEVEL:,}`\n"
                f"`{bar_top1}` `{pct_top1:.1f}%`\n"
                f"Faltan: `{pv1}` PVS + `{sh1}` SHULKERS"
            ),
            inline=False
        )

        embed.add_field(
            name="📈 META TOP 3",
            value=(
                f"`{nivel_estimado:,} / {TARGET_TOP3_LEVEL:,}`\n"
                f"`{bar_top3}` `{pct_top3:.1f}%`\n"
                f"Faltan: `{pv3}` PVS + `{sh3}` SHULKERS"
            ),
            inline=False
        )

        embed.set_footer(text=f"Base exacta: {base_level:,} | Desde: {base_date or 'sin calibrar'}")

        msg = await obtener_mensaje_fijo(channel, "panel_progreso")
        await msg.edit(content=None, embed=embed)
        print("✅ Panel de progreso actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_panel_progreso: {e}")

# ===============================
# COMANDOS
# ===============================
@bot.command(name="setnivel")
@commands.has_permissions(administrator=True)
async def setnivel(ctx, nivel: int):
    set_progress_value("base_level", str(nivel))
    set_progress_value("base_shulkers", str(total_shulkers_all_time()))
    set_progress_value("base_date", str(date.today()))
    await ctx.reply(
        f"✅ Nivel base fijado en `{nivel:,}` y progreso recalibrado.",
        mention_author=False
    )
    await actualizar_panel_progreso()

@bot.command(name="estadoisla")
@commands.has_permissions(administrator=True)
async def estadoisla(ctx):
    base_level = int(get_progress_value("base_level", "0") or 0)
    base_shulkers = int(get_progress_value("base_shulkers", "0") or 0)
    base_date = get_progress_value("base_date", "sin fecha")

    total_sh = total_shulkers_all_time()
    nuevos_sh = max(0, total_sh - base_shulkers)
    niveles_ganados = nuevos_sh * LEVELS_PER_SHULKER
    nivel_estimado = base_level + niveles_ganados

    embed = discord.Embed(
        title="📊 Estado actual de la isla",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Base nivel", value=f"`{base_level:,}`", inline=False)
    embed.add_field(name="Base shulkers", value=f"`{base_shulkers:,}`", inline=True)
    embed.add_field(name="Shulkers totales", value=f"`{total_sh:,}`", inline=True)
    embed.add_field(name="Shulkers desde base", value=f"`{nuevos_sh:,}`", inline=True)
    embed.add_field(name="Niveles ganados", value=f"`{niveles_ganados:,}`", inline=True)
    embed.add_field(name="Nivel estimado", value=f"`{nivel_estimado:,}`", inline=True)
    embed.set_footer(text=f"Base tomada desde: {base_date}")
    await ctx.reply(embed=embed, mention_author=False)

# ===============================
# RANKINGS
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
            user = cortar_nombre(user, 20)

            if i == 1:
                medalla = "🥇"
            elif i == 2:
                medalla = "🥈"
            elif i == 3:
                medalla = "🥉"
            else:
                medalla = "▫️"

            porcentaje = int(round((total / maximo) * 100)) if maximo > 0 else 0
            bar = barra_progreso(total, maximo, largo=12)

            lines.append(
                f"{medalla} **{user}**\n\n"
                f"{total} shulkers • {porcentaje}%\n"
                f"{bar}"
            )

        ranking_text = "\n\n".join(lines)

    stacks, niveles, pv, resto = equivalencias(total_periodo_shulkers)

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

async def total_periodo(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) FROM shulker WHERE {where_sql}", params)
    return int(cursor.fetchone()[0] or 0)

async def actualizar_todos_los_ranking():
    try:
        channel = bot.get_channel(RANKING_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró RANKING_CHANNEL_ID")
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

        embed_mensual = await crear_embed_ranking(
            "TOP MENSUAL", "👑", discord.Color.purple(), mensual, "Mes actual", total_mensual
        )
        embed_semanal = await crear_embed_ranking(
            "TOP SEMANAL", "📈", discord.Color.blue(), semanal, f"Desde {inicio_semana}", total_semanal
        )
        embed_diario = await crear_embed_ranking(
            "TOP DIARIO", "⚡", discord.Color.gold(), diario, f"Hoy • {hoy}", total_diario
        )

        msg_mensual = await obtener_mensaje_fijo(channel, "ranking_mensual")
        msg_semanal = await obtener_mensaje_fijo(channel, "ranking_semanal")
        msg_diario = await obtener_mensaje_fijo(channel, "ranking_diario")

        await msg_mensual.edit(content=None, embed=embed_mensual)
        await msg_semanal.edit(content=None, embed=embed_semanal)
        await msg_diario.edit(content=None, embed=embed_diario)
        print("✅ Rankings shulker actualizados")
    except Exception as e:
        print(f"❌ Error en actualizar_todos_los_ranking: {e}")

# ===============================
# RANKINGS END APORTADA
# ===============================
async def total_periodo_end(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) FROM end_aportado WHERE {where_sql}", params)
    return int(cursor.fetchone()[0] or 0)

async def actualizar_rankings_end():
    try:
        channel = bot.get_channel(END_APORTE_RANKING_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró END_APORTE_RANKING_CHANNEL_ID")
            return

        hoy = date.today()
        inicio_mes = clamp_start(hoy.replace(day=1))
        inicio_semana = clamp_start(hoy - timedelta(days=hoy.weekday()))

        cursor.execute("""
            SELECT username, SUM(total) as s
            FROM end_aportado
            WHERE fecha >= ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_mes),))
        mensual = cursor.fetchall()
        total_mensual = await total_periodo_end("fecha >= ?", (str(inicio_mes),))

        cursor.execute("""
            SELECT username, SUM(total) as s
            FROM end_aportado
            WHERE fecha >= ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_semana),))
        semanal = cursor.fetchall()
        total_semanal = await total_periodo_end("fecha >= ?", (str(inicio_semana),))

        cursor.execute("""
            SELECT username, SUM(total) as s
            FROM end_aportado
            WHERE fecha = ? AND fecha >= ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(hoy), str(BOT_START_DATE)))
        diario = cursor.fetchall()
        total_diario = await total_periodo_end("fecha = ? AND fecha >= ?", (str(hoy), str(BOT_START_DATE)))

        embed_mensual = await crear_embed_ranking(
            "END APORTADA • TOP MENSUAL", "🪨", discord.Color.dark_gray(), mensual, "Mes actual", total_mensual
        )
        embed_semanal = await crear_embed_ranking(
            "END APORTADA • TOP SEMANAL", "📈", discord.Color.blue(), semanal, f"Desde {inicio_semana}", total_semanal
        )
        embed_diario = await crear_embed_ranking(
            "END APORTADA • TOP DIARIO", "⚡", discord.Color.gold(), diario, f"Hoy • {hoy}", total_diario
        )

        msg_mensual = await obtener_mensaje_fijo(channel, "ranking_end_aportada_mensual")
        msg_semanal = await obtener_mensaje_fijo(channel, "ranking_end_aportada_semanal")
        msg_diario = await obtener_mensaje_fijo(channel, "ranking_end_aportada_diario")

        await msg_mensual.edit(content=None, embed=embed_mensual)
        await msg_semanal.edit(content=None, embed=embed_semanal)
        await msg_diario.edit(content=None, embed=embed_diario)
        print("✅ Rankings END APORTADA actualizados")
    except Exception as e:
        print(f"❌ Error en actualizar_rankings_end: {e}")

# ===============================
# TASK
# ===============================
@tasks.loop(hours=1)
async def ranking_automatico():
    await actualizar_todos_los_ranking()
    await actualizar_panel_progreso()
    await actualizar_rankings_end()

# ===============================
# MODAL SHULKER NORMAL
# ===============================
class ShulkerModal(discord.ui.Modal, title="Registro de Shulker"):
    cantidad = discord.ui.TextInput(label="¿Cuántas shulker colocaste?", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = interaction.user.id
            ahora = time.time()

            if user_id in cooldowns and ahora - cooldowns[user_id] < COOLDOWN_SECONDS:
                restante = int(COOLDOWN_SECONDS - (ahora - cooldowns[user_id]))
                await interaction.response.send_message(
                    f"⏳ Espera `{restante}` segundos antes de registrar otra vez.",
                    ephemeral=True
                )
                return

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

            cooldowns[user_id] = ahora

            cursor.execute(
                "SELECT total FROM shulker WHERE user_id = ? AND fecha = ?",
                (user_id, hoy)
            )
            row = cursor.fetchone()
            nuevo_total = int(row[0] or 0) if row else cantidad_int

            await actualizar_todos_los_ranking()
            await actualizar_panel_progreso()

            end_channel = interaction.client.get_channel(END_CHANNEL_ID)
            if end_channel:
                embed = discord.Embed(
                    title="📦 Registro de Shulker",
                    description=(
                        f"👤 {interaction.user.mention}\n"
                        f"➕ Registró: `{cantidad_int}`\n"
                        f"📊 Total hoy: `{nuevo_total}`"
                    ),
                    color=discord.Color.green()
                )
                await end_channel.send(embed=embed)

            await interaction.response.send_message(
                f"✅ Registro guardado. Añadiste `{cantidad_int}` shulkers. Total de hoy: `{nuevo_total}`.",
                ephemeral=True
            )
            print(f"✅ Registro guardado para {username}: +{cantidad_int}")
        except Exception as e:
            print(f"❌ Error en modal on_submit: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error al guardar el registro.",
                    ephemeral=True
                )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        print(f"❌ Error en ShulkerModal.on_error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Ocurrió un error en el formulario.",
                ephemeral=True
            )

# ===============================
# MODAL END APORTADA
# ===============================
class EndAportadoModal(discord.ui.Modal, title="Registro de End Aportada"):
    cantidad = discord.ui.TextInput(
        label="¿Cuántas shulkers de End aportaste?",
        required=True,
        max_length=8
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = interaction.user.id
            ahora = time.time()

            pendiente = get_pending_end(user_id)
            if pendiente:
                restante = int(max(1, END_UPLOAD_TIMEOUT_SECONDS - (ahora - pendiente["timestamp"])))
                await interaction.response.send_message(
                    f"⏳ Ya tienes un registro pendiente de evidencia. "
                    f"Sube la imagen en este canal o espera `{restante}` segundos para que expire.",
                    ephemeral=True
                )
                return

            if user_id in end_aporte_cooldowns and ahora - end_aporte_cooldowns[user_id] < COOLDOWN_SECONDS:
                restante = int(COOLDOWN_SECONDS - (ahora - end_aporte_cooldowns[user_id]))
                await interaction.response.send_message(
                    f"⏳ Espera `{restante}` segundos antes de registrar otra aportación.",
                    ephemeral=True
                )
                return

            try:
                cantidad_int = int(self.cantidad.value)
                if cantidad_int <= 0:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "❌ Número inválido. Debes escribir un número entero mayor que 0.",
                    ephemeral=True
                )
                return

            end_upload_pending[user_id] = {
                "cantidad": cantidad_int,
                "timestamp": ahora,
                "channel_id": interaction.channel.id if interaction.channel else 0,
                "fecha": str(date.today()),
                "username": interaction.user.display_name
            }

            end_aporte_cooldowns[user_id] = ahora

            await interaction.response.send_message(
                "📸 **Paso 2/2:** ahora sube **una imagen** como evidencia de la End farmeada.\n"
                f"⏳ Tienes `{END_UPLOAD_TIMEOUT_SECONDS}` segundos.\n"
                "✅ Cuando la subas, se enviará al canal privado de staff para revisión.\n"
                "⚠️ Solo lo aprobado contará en el top público.",
                ephemeral=True
            )

        except Exception as e:
            print(f"❌ Error en EndAportadoModal.on_submit: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error al iniciar el registro de End aportada.",
                    ephemeral=True
                )

# ===============================
# BOTÓN SHULKER NORMAL
# ===============================
class ShulkerButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar Shulker",
        style=discord.ButtonStyle.green,
        emoji="📦",
        custom_id="registrar_shulker_btn_v1"
    )
    async def registrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(ShulkerModal())
            print(f"✅ Botón pulsado por {interaction.user}")
        except Exception as e:
            print(f"❌ Error al abrir modal: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ No se pudo abrir el formulario.",
                    ephemeral=True
                )

# ===============================
# BOTÓN END APORTADA
# ===============================
class EndAportadoButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar End Aportada",
        style=discord.ButtonStyle.blurple,
        emoji="🪨",
        custom_id="registrar_end_aportada_btn_v1"
    )
    async def registrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(EndAportadoModal())
            print(f"✅ Botón End Aportada pulsado por {interaction.user}")
        except Exception as e:
            print(f"❌ Error al abrir modal End Aportada: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ No se pudo abrir el formulario.",
                    ephemeral=True
                )

# ===============================
# VIEW DE REVISIÓN STAFF
# ===============================
class EndReviewView(discord.ui.View):
    def __init__(self, disabled: bool = False):
        super().__init__(timeout=None)
        for item in self.children:
            item.disabled = disabled

    @discord.ui.button(
        label="Aprobar",
        style=discord.ButtonStyle.green,
        emoji="✅",
        custom_id="end_review_approve_v1"
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, approve=True)

    @discord.ui.button(
        label="Rechazar",
        style=discord.ButtonStyle.red,
        emoji="❌",
        custom_id="end_review_reject_v1"
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, approve=False)

    async def handle_review(self, interaction: discord.Interaction, approve: bool):
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("❌ No se pudo validar tu permiso.", ephemeral=True)
                return

            if not is_staff_member(interaction.user):
                await interaction.response.send_message(
                    "❌ Solo el staff puede aprobar o rechazar solicitudes.",
                    ephemeral=True
                )
                return

            request_row = get_request_by_review_message_id(interaction.message.id)
            if not request_row:
                await interaction.response.send_message(
                    "❌ No se encontró la solicitud asociada a este mensaje.",
                    ephemeral=True
                )
                return

            request_id = request_row[0]
            status_actual = request_row[6]

            if status_actual != "pending":
                await interaction.response.send_message(
                    f"⚠️ Esta solicitud ya fue revisada anteriormente (`{status_actual}`).",
                    ephemeral=True
                )
                return

            reviewed_at = now_str()
            reviewed_by = interaction.user.id

            if approve:
                cursor.execute("""
                    UPDATE end_requests
                    SET status = 'approved',
                        reviewed_by = ?,
                        reviewed_at = ?
                    WHERE id = ? AND status = 'pending'
                """, (reviewed_by, reviewed_at, request_id))

                if cursor.rowcount == 0:
                    db.commit()
                    await interaction.response.send_message(
                        "⚠️ Esta solicitud ya no estaba pendiente.",
                        ephemeral=True
                    )
                    return

                user_id = request_row[1]
                username = request_row[2]
                fecha_registro = request_row[3]
                cantidad = request_row[4]

                cursor.execute("""
                    INSERT INTO end_aportado (user_id, username, fecha, total)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, fecha)
                    DO UPDATE SET
                        total = total + excluded.total,
                        username = excluded.username
                """, (user_id, username, fecha_registro, cantidad))

                db.commit()

                updated_row = get_request_by_id(request_id)
                embed = construir_embed_revision(
                    updated_row,
                    estado_override="approved",
                    reviewer_name=interaction.user.display_name
                )

                await interaction.response.edit_message(
                    embed=embed,
                    view=EndReviewView(disabled=True)
                )

                try:
                    usuario = await bot.fetch_user(user_id)
                    await usuario.send(
                        f"✅ Tu solicitud de **End aportada** por `{cantidad}` shulkers fue **aprobada**."
                    )
                except Exception:
                    pass

                await actualizar_rankings_end()
                print(f"✅ Solicitud END #{request_id} aprobada por {interaction.user}")

            else:
                cursor.execute("""
                    UPDATE end_requests
                    SET status = 'rejected',
                        reviewed_by = ?,
                        reviewed_at = ?
                    WHERE id = ? AND status = 'pending'
                """, (reviewed_by, reviewed_at, request_id))

                if cursor.rowcount == 0:
                    db.commit()
                    await interaction.response.send_message(
                        "⚠️ Esta solicitud ya no estaba pendiente.",
                        ephemeral=True
                    )
                    return

                db.commit()

                updated_row = get_request_by_id(request_id)
                embed = construir_embed_revision(
                    updated_row,
                    estado_override="rejected",
                    reviewer_name=interaction.user.display_name
                )

                await interaction.response.edit_message(
                    embed=embed,
                    view=EndReviewView(disabled=True)
                )

                user_id = request_row[1]
                cantidad = request_row[4]

                try:
                    usuario = await bot.fetch_user(user_id)
                    await usuario.send(
                        f"❌ Tu solicitud de **End aportada** por `{cantidad}` shulkers fue **rechazada** por el staff."
                    )
                except Exception:
                    pass

                print(f"❌ Solicitud END #{request_id} rechazada por {interaction.user}")

        except Exception as e:
            print(f"❌ Error en EndReviewView.handle_review: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error al revisar la solicitud.",
                    ephemeral=True
                )

# ===============================
# CAPTURA DE IMAGEN PARA END APORTADA
# ===============================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    try:
        pendiente = get_pending_end(message.author.id)

        if pendiente:
            # Expirado
            if not pendiente:
                await bot.process_commands(message)
                return

            # Exigir el mismo canal donde abrió el modal
            canal_esperado = pendiente["channel_id"]
            if canal_esperado and message.channel.id != canal_esperado:
                await bot.process_commands(message)
                return

            # Si escribió texto sin imagen en el canal correcto, recordatorio
            if not message.attachments:
                if message.channel.id == canal_esperado:
                    await message.reply(
                        "📸 Aún estoy esperando tu **imagen de evidencia** para completar el registro de End aportada."
                    )
                await bot.process_commands(message)
                return

            attachment = message.attachments[0]

            if not attachment.content_type or not attachment.content_type.startswith("image"):
                await message.reply("❌ Debes subir **una imagen válida** como evidencia.")
                await bot.process_commands(message)
                return

            cantidad = pendiente["cantidad"]
            fecha_registro = pendiente["fecha"]
            username = pendiente["username"]
            user_id = message.author.id

            review_channel = bot.get_channel(END_APORTE_REVIEW_CHANNEL_ID)
            if not review_channel:
                await message.reply("❌ No se encontró el canal privado de revisión del staff.")
                await bot.process_commands(message)
                return

            created_at = now_str()

            cursor.execute("""
                INSERT INTO end_requests (
                    user_id, username, fecha, cantidad, image_url, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (user_id, username, fecha_registro, cantidad, attachment.url, created_at))
            db.commit()

            request_id = cursor.lastrowid
            request_row = get_request_by_id(request_id)

            embed = construir_embed_revision(request_row)
            review_msg = await review_channel.send(embed=embed, view=EndReviewView())

            cursor.execute("""
                UPDATE end_requests
                SET review_message_id = ?
                WHERE id = ?
            """, (review_msg.id, request_id))
            db.commit()

            limpiar_pendiente_end(user_id)

            await message.reply(
                f"✅ Tu evidencia fue enviada al **staff** para revisión.\n"
                f"🆔 Solicitud: `#{request_id}`\n"
                "⏳ Cuando sea aprobada, aparecerá en el top público."
            )

            print(f"✅ Solicitud END #{request_id} creada por {message.author}")

    except Exception as e:
        print(f"❌ Error en on_message: {e}")

    await bot.process_commands(message)

# ===============================
# READY
# ===============================
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    print(f"📂 DB: {DB_PATH}")
    print(f"📌 Start date: {BOT_START_DATE}")
    print(f"✅ LEVELS_PER_SHULKER: {LEVELS_PER_SHULKER} | LEVELS_PER_PV: {LEVELS_PER_PV}")
    print("✅ BOT VERSION NUEVA CARGADA")

    asegurar_base_progreso_si_falta()

    if not ranking_automatico.is_running():
        ranking_automatico.start()

    # Botón fijo shulker normal
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
        print("✅ Botón fijo shulker actualizado")

    # Botón fijo END aportada
    end_aporte_form_channel = bot.get_channel(END_APORTE_FORM_CHANNEL_ID)
    if end_aporte_form_channel:
        msg = await obtener_mensaje_fijo(end_aporte_form_channel, "form_boton_end_aportada")
        await msg.edit(
            content=None,
            embed=discord.Embed(
                title="🪨 Registro de End Aportada",
                description=(
                    "Presiona el botón para registrar tu End aportada.\n\n"
                    "📌 Flujo:\n"
                    "1. Abres el formulario\n"
                    "2. Escribes la cantidad\n"
                    "3. Subes una imagen como evidencia\n"
                    "4. El staff revisa\n"
                    "5. Solo lo aprobado entra al top público"
                ),
                color=discord.Color.blurple()
            ),
            view=EndAportadoButton()
        )
        print("✅ Botón fijo END aportada actualizado")

    await actualizar_todos_los_ranking()
    await actualizar_panel_progreso()
    await actualizar_rankings_end()

# ===============================
# ERROR GLOBAL
# ===============================
@bot.event
async def on_command_error(ctx, error):
    print(f"❌ Error de comando: {error}")

# ===============================
# RUN
# ===============================
if not TOKEN:
    raise ValueError("Falta la variable de entorno DISCORD_TOKEN")

bot.run(TOKEN)
