import os
import asyncio
import io
import sqlite3
import discord
import shutil
from zoneinfo import ZoneInfo
from datetime import date, timedelta, datetime
from discord.ext import commands, tasks

# ===============================
# CONFIGURACIÓN
# ===============================
FORM_CHANNEL_ID = 1465764092978532547
RANKING_CHANNEL_ID = 1468791225619320894
END_CHANNEL_ID = 1462316362515873947
PROGRESS_CHANNEL_ID = 1478948711995412702
STATS_PANEL_CHANNEL_ID = 1495791333095506035

# ===============================
# NUEVO SISTEMA END APORTADA
# ===============================
END_APORTE_FORM_CHANNEL_ID = 1482278081552187435
END_APORTE_RANKING_CHANNEL_ID = 1482278329871503461
END_APORTE_REVIEW_CHANNEL_ID = 1482278518552530955

# Canal opcional para logs del staff
# Pon 0 si no quieres usarlo
STAFF_LOG_CHANNEL_ID = 1462316363552133202

# ===============================
# CANALES NUEVOS (ISLA SECUNDARIA + EVENTO)
# ===============================
SECUNDARIA_RANKING_CHANNEL_ID = 1531562565287673856
EVENTO_RANKING_CHANNEL_ID = 1531562510040305715

# Canal staff para panel de stock de End en almacén (pon el ID real)
# Si queda en 0, igual funcionan los comandos !stock*
STOCK_CHANNEL_ID = 1534806401606484149

# Canal público de cumpleaños del team (pon el ID real)
BIRTHDAY_CHANNEL_ID = 1534821447141294080

# ===============================
# FUNCIONES DEL TEAM (multi-rol)
# ===============================
FUNCIONES_CHANNEL_ID = 1535185391727157398
ROLE_MINERO_ID = 1535184004721156166
ROLE_OBRERO_ID = 1535184284938149888
ROLE_CONSTRUCTOR_ID = 1535184214448799864

# key -> (role_id, emoji, label, descripcion)
FUNCIONES_TEAM = {
    "minero": (ROLE_MINERO_ID, "⛏️", "Minero", "Farmea y aporta End"),
    "obrero": (ROLE_OBRERO_ID, "🧱", "Obrero", "Coloca End en las islas"),
    "constructor": (ROLE_CONSTRUCTOR_ID, "🏗️", "Constructor", "Construye en las islas"),
}

# Mínimos semanales (1 PV = 27 shulkers)
MIN_SHULKERS_MINERO_SEMANA = 27   # 1 PV End APORTADA
MIN_SHULKERS_OBRERO_SEMANA = 27   # 1 PV End COLOCADA (principal+secundaria)
MAX_STRIKES_FUNCION = 3           # 3ª semana sin cumplir → restringido
ROLE_RESTRINGIDO_ID = 0           # opcional; 0 = solo marca en DB
# Al elegir Minero/Obrero no pueden quitarse el rol N días (anti-evasión del mínimo)
FUNCION_LOCK_DAYS = 8
# Mínimos solo cuentan semanas que TERMINEN en o después de esta fecha
MINIMOS_ACTIVE_FROM = date(2026, 8, 10)

TOKEN = os.getenv("DISCORD_TOKEN")

COOLDOWN_SECONDS = 60
END_UPLOAD_TIMEOUT_SECONDS = 120
PUBLIC_EVIDENCE_DELETE_SECONDS = 60

TARGET_NEXT_LEVEL = 250_000_000
DAILY_SHULKER_GOAL = 120

# ===============================
# EVENTO ACTUAL (se sincroniza con DB al iniciar)
# ===============================
EVENTO_START_DATE = date(2026, 7, 25)
EVENTO_GOAL_PVS = 40
EVENTO_NOMBRE = "Evento 40 PVs"
EVENTO_STATUS = "active"  # active | ended | none
EVENTO_TIPO = "end"  # end | ranking | custom
EVENTO_RECOMPENSAS = ""
EVENTO_REGLAS = ""
EVENTO_PARTICIPANTES = "Todo el team"
EVENTO_OBJETIVO = "Colocar End hasta completar la meta de PVs"

# ===============================
# ZONA HORARIA
# ===============================
CHILE_TZ = ZoneInfo("America/Santiago")
UTC_TZ = ZoneInfo("UTC")

# ===============================
# CONVERSIÓN EXACTA SEGÚN TU SERVER
# ===============================
LEVELS_PER_BLOCK = 9
LEVELS_PER_STACK = 64 * LEVELS_PER_BLOCK          # 576
LEVELS_PER_SHULKER = 27 * LEVELS_PER_STACK        # 15552
SHULKERS_PER_PV = 27
LEVELS_PER_PV = SHULKERS_PER_PV * LEVELS_PER_SHULKER  # 419904

EVENTO_GOAL_SHULKERS = EVENTO_GOAL_PVS * SHULKERS_PER_PV  # 1080

DEFAULT_BASE_LEVEL = 42127075
DEFAULT_BASE_LEVEL_SECUNDARIA = 0  # Calibrar con !setnivel <nivel> secundaria

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
db.execute("PRAGMA temp_store=MEMORY;")
db.execute("PRAGMA cache_size=-8000;")  # ~8MB de cache
db.execute("PRAGMA mmap_size=268435456;")  # 256MB mmap
db.row_factory = sqlite3.Row
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
        self.add_view(StatsPanelView())
        self.add_view(BirthdayPanelView())
        self.add_view(FuncionesPanelView())
        print("✅ Vistas persistentes registradas en setup_hook")

bot = ShulkerBot(command_prefix="!", intents=intents)

# ===============================
# FECHAS / TIEMPO
# ===============================
def utc_now() -> datetime:
    return discord.utils.utcnow()

def local_now() -> datetime:
    return utc_now().astimezone(CHILE_TZ)

def today_local() -> date:
    return local_now().date()

def local_date_str() -> str:
    return str(today_local())

def local_datetime_str() -> str:
    return local_now().strftime("%Y-%m-%d %H:%M:%S %Z")

def iso_utc_now() -> str:
    return utc_now().isoformat()

def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

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
    rejection_reason TEXT,
    review_message_id INTEGER,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_cooldowns (
    feature TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (feature, user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS pending_end_uploads (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    fecha TEXT NOT NULL,
    cantidad INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS staff_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    request_id INTEGER,
    target_user_id INTEGER,
    target_username TEXT,
    actor_user_id INTEGER,
    actor_username TEXT,
    amount INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL
)
""")

# Tablas separadas para Isla Secundaria y Evento
cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker_secundaria (
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, fecha)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker_evento (
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, fecha)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS birthdays (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    day INTEGER NOT NULL,
    month INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS member_functions (
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    funcion TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (user_id, funcion)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS function_strikes (
    user_id INTEGER NOT NULL,
    funcion TEXT NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 0,
    last_fail_week TEXT,
    last_ok_week TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_id, funcion)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS member_status (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    restricted_at TEXT,
    reason TEXT
)
""")

# Índices para consultas rápidas de rankings
for tabla in ("shulker", "shulker_secundaria", "shulker_evento", "end_aportado"):
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_fecha ON {tabla}(fecha)")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_user_fecha ON {tabla}(user_id, fecha)")

db.commit()

# ===============================
# START DATE
# ===============================
def get_bot_start_date() -> date:
    cursor.execute("SELECT value FROM bot_config WHERE key='start_date'")
    row = cursor.fetchone()
    if row and row["value"]:
        return date.fromisoformat(row["value"])

    start = today_local().replace(day=1)
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
    pk_cols = [c["name"] for c in cols if c["pk"] > 0]

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
# MIGRACIONES ADICIONALES
# ===============================
def asegurar_columna_si_no_existe(tabla: str, columna: str, definicion: str):
    cursor.execute(f"PRAGMA table_info({tabla})")
    cols = [r["name"] for r in cursor.fetchall()]
    if columna not in cols:
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
        db.commit()
        print(f"✅ Columna agregada: {tabla}.{columna}")

asegurar_columna_si_no_existe("end_requests", "rejection_reason", "TEXT")
asegurar_columna_si_no_existe("end_requests", "ubicacion", "TEXT")
asegurar_columna_si_no_existe("pending_end_uploads", "ubicacion", "TEXT")
asegurar_columna_si_no_existe("member_functions", "locked_until", "TEXT")

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
    ratio = max(0.0, min(1.0, valor / maximo))
    llenos = int(round(ratio * largo))
    llenos = max(0, min(largo, llenos))
    return "▰" * llenos + "▱" * (largo - llenos)

def barra_meta(valor: int, meta: int, largo: int = 20) -> str:
    if meta <= 0:
        return "▱" * largo
    ratio = max(0.0, min(1.0, valor / meta))
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
    return row["value"] if row and row["value"] is not None else default

def set_progress_value(key: str, value: str):
    cursor.execute(
        "INSERT OR REPLACE INTO island_progress (key, value) VALUES (?, ?)",
        (key, value)
    )
    db.commit()

def total_shulkers_all_time() -> int:
    cursor.execute("SELECT COALESCE(SUM(total), 0) AS total FROM shulker")
    row = cursor.fetchone()
    return int(row["total"] or 0)

def total_shulkers_today() -> int:
    hoy = local_date_str()
    cursor.execute("SELECT COALESCE(SUM(total), 0) AS total FROM shulker WHERE fecha = ?", (hoy,))
    row = cursor.fetchone()
    return int(row["total"] or 0)

def total_evento_shulkers() -> int:
    cursor.execute(
        "SELECT COALESCE(SUM(total), 0) AS total FROM shulker_evento WHERE fecha >= ?",
        (str(EVENTO_START_DATE),)
    )
    row = cursor.fetchone()
    return int(row["total"] or 0)


def get_evento_top(limit: int = 10):
    cursor.execute("""
        SELECT user_id, username, SUM(total) as s
        FROM shulker_evento
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT ?
    """, (str(EVENTO_START_DATE), limit))
    return cursor.fetchall()


def get_evento_ganador():
    rows = get_evento_top(1)
    if not rows:
        return None
    return rows[0]


def is_evento_activo() -> bool:
    return EVENTO_STATUS == "active"


def evento_usa_registro_shulker() -> bool:
    """Solo eventos tipo 'end' permiten registrar shulkers en el menú."""
    return is_evento_activo() and EVENTO_TIPO == "end"


def _label_tipo_evento(tipo: str | None = None) -> str:
    t = (tipo or EVENTO_TIPO or "custom").lower()
    return {
        "end": "🪨 End / Shulkers (registro en el bot)",
        "ranking": "🎮 Ranking del juego (battle pass, tops, etc.)",
        "custom": "⭐ Evento personalizado",
    }.get(t, t)


def guardar_config_evento():
    pairs = {
        "event_status": EVENTO_STATUS,
        "event_name": EVENTO_NOMBRE,
        "event_type": EVENTO_TIPO,
        "event_goal_pvs": str(EVENTO_GOAL_PVS),
        "event_start_date": str(EVENTO_START_DATE),
        "event_rewards": EVENTO_RECOMPENSAS or "",
        "event_rules": EVENTO_REGLAS or "",
        "event_participants": EVENTO_PARTICIPANTES or "",
        "event_objective": EVENTO_OBJETIVO or "",
    }
    for k, v in pairs.items():
        cursor.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
            (k, v)
        )
    db.commit()


def cargar_config_evento():
    global EVENTO_STATUS, EVENTO_NOMBRE, EVENTO_GOAL_PVS, EVENTO_GOAL_SHULKERS
    global EVENTO_START_DATE, EVENTO_RECOMPENSAS, EVENTO_REGLAS, EVENTO_PARTICIPANTES
    global EVENTO_TIPO, EVENTO_OBJETIVO

    def _get(key, default=""):
        cursor.execute("SELECT value FROM bot_config WHERE key=?", (key,))
        row = cursor.fetchone()
        return row["value"] if row and row["value"] is not None else default

    status = _get("event_status", "")
    if not status:
        guardar_config_evento()
        return

    EVENTO_STATUS = status
    EVENTO_NOMBRE = _get("event_name", EVENTO_NOMBRE) or EVENTO_NOMBRE
    EVENTO_TIPO = (_get("event_type", EVENTO_TIPO) or EVENTO_TIPO).lower()
    if EVENTO_TIPO not in ("end", "ranking", "custom"):
        EVENTO_TIPO = "end"
    try:
        EVENTO_GOAL_PVS = int(_get("event_goal_pvs", str(EVENTO_GOAL_PVS)) or EVENTO_GOAL_PVS)
    except ValueError:
        pass
    EVENTO_GOAL_SHULKERS = EVENTO_GOAL_PVS * SHULKERS_PER_PV
    try:
        sd = _get("event_start_date", str(EVENTO_START_DATE))
        if sd:
            EVENTO_START_DATE = date.fromisoformat(sd)
    except ValueError:
        pass
    EVENTO_RECOMPENSAS = _get("event_rewards", "")
    EVENTO_REGLAS = _get("event_rules", "")
    EVENTO_PARTICIPANTES = _get("event_participants", EVENTO_PARTICIPANTES)
    EVENTO_OBJETIVO = _get("event_objective", EVENTO_OBJETIVO)

def total_secundaria_all_time() -> int:
    cursor.execute("SELECT COALESCE(SUM(total), 0) AS total FROM shulker_secundaria")
    row = cursor.fetchone()
    return int(row["total"] or 0)

def total_secundaria_today() -> int:
    hoy = local_date_str()
    cursor.execute(
        "SELECT COALESCE(SUM(total), 0) AS total FROM shulker_secundaria WHERE fecha = ?",
        (hoy,)
    )
    row = cursor.fetchone()
    return int(row["total"] or 0)

def total_secundaria_periodo(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) AS total FROM shulker_secundaria WHERE {where_sql}", params)
    row = cursor.fetchone()
    return int(row["total"] or 0)


# ===============================
# STOCK DE END EN ALMACÉN
# ===============================
def _stock_key(destino: str) -> str | None:
    d = (destino or "").strip().lower()
    if d in ("principal", "isla", "main"):
        return "stock_principal"
    if d in ("secundaria", "segunda", "sec"):
        return "stock_secundaria"
    return None


def get_stock_shulkers(destino: str) -> int:
    key = _stock_key(destino)
    if not key:
        return 0
    try:
        return max(0, int(get_progress_value(key, "0") or 0))
    except ValueError:
        return 0


def set_stock_shulkers(destino: str, cantidad: int) -> int:
    key = _stock_key(destino)
    if not key:
        raise ValueError("destino inválido")
    cantidad = max(0, int(cantidad))
    set_progress_value(key, str(cantidad))
    return cantidad


def add_stock_shulkers(destino: str, cantidad: int) -> int:
    if cantidad <= 0:
        return get_stock_shulkers(destino)
    nuevo = get_stock_shulkers(destino) + int(cantidad)
    return set_stock_shulkers(destino, nuevo)


def remove_stock_shulkers(destino: str, cantidad: int) -> tuple[int, int]:
    """
    Resta del stock sin bajar de 0.
    Returns: (cantidad_realmente_restada, stock_restante)
    """
    if cantidad <= 0:
        actual = get_stock_shulkers(destino)
        return 0, actual
    actual = get_stock_shulkers(destino)
    restado = min(actual, int(cantidad))
    nuevo = actual - restado
    set_stock_shulkers(destino, nuevo)
    return restado, nuevo


def format_stock_line(destino_label: str, shulkers: int) -> str:
    pv, resto = shulkers_a_pv_y_shulkers(shulkers)
    return (
        f"**{destino_label}:** `{shulkers:,}` shulkers "
        f"(`{pv}` PVs + `{resto}` shulkers)"
    )


def parse_cantidad_stock(cantidad: int, unidad: str | None = None) -> int:
    """Convierte a shulkers. unidad: shulker(s) | pv(s)"""
    u = (unidad or "shulker").strip().lower()
    if u in ("pv", "pvs"):
        return int(cantidad) * SHULKERS_PER_PV
    return int(cantidad)


async def actualizar_panel_stock():
    try:
        if not STOCK_CHANNEL_ID:
            return
        channel = bot.get_channel(STOCK_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró STOCK_CHANNEL_ID")
            return

        sp = get_stock_shulkers("principal")
        ss = get_stock_shulkers("secundaria")
        total = sp + ss
        tpv, trest = shulkers_a_pv_y_shulkers(total)

        embed = discord.Embed(
            title="📦 STOCK DE END — ALMACÉN",
            description=(
                "Contabilidad de End disponible en almacén del team.\n"
                "Al registrar shulkers **puestas** el miembro elige si salieron del almacén o eran propias."
            ),
            color=discord.Color.dark_blue(),
            timestamp=utc_now()
        )
        embed.add_field(
            name="🏝️ Isla Principal",
            value=format_stock_line("Stock", sp).replace("**Stock:** ", ""),
            inline=False
        )
        embed.add_field(
            name="🌿 Isla Secundaria",
            value=format_stock_line("Stock", ss).replace("**Stock:** ", ""),
            inline=False
        )
        embed.add_field(
            name="Σ Total almacén",
            value=f"`{total:,}` shulkers (`{tpv}` PVs + `{trest}`)",
            inline=False
        )
        embed.set_footer(text="Staff: !stocksumar | !stockquitar | !stockset | Hora Chile")

        msg = await obtener_mensaje_fijo(channel, "panel_stock_end")
        await msg.edit(content=None, embed=embed)
        print("✅ Panel de stock actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_panel_stock: {e}")


# ===============================
# CUMPLEAÑOS DEL TEAM
# ===============================
MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
MESES_EMOJI = {
    1: "❄️", 2: "💝", 3: "🌸", 4: "🌧️",
    5: "🌷", 6: "☀️", 7: "🏖️", 8: "🌟",
    9: "🍂", 10: "🎃", 11: "🍁", 12: "🎄",
}


def parse_cumple_ddmm(texto: str) -> tuple[int, int] | None:
    t = (texto or "").strip().replace(" ", "")
    for sep in ("/", "-", "."):
        if sep in t:
            parts = t.split(sep)
            if len(parts) >= 2:
                try:
                    d, m = int(parts[0]), int(parts[1])
                    if 1 <= m <= 12 and 1 <= d <= 31:
                        date(2024, m, d)  # valida día (29/02 ok en bisiesto)
                        return d, m
                except ValueError:
                    return None
    return None


def next_birthday_date(day: int, month: int, from_date: date | None = None) -> date:
    base = from_date or today_local()
    try:
        este_anio = date(base.year, month, day)
    except ValueError:
        este_anio = date(base.year, month, min(day, 28))
    if este_anio >= base:
        return este_anio
    try:
        return date(base.year + 1, month, day)
    except ValueError:
        return date(base.year + 1, month, min(day, 28))


def days_until_birthday(day: int, month: int) -> int:
    return (next_birthday_date(day, month) - today_local()).days


def set_birthday(user_id: int, username: str, day: int, month: int):
    now = local_datetime_str()
    cursor.execute("""
        INSERT INTO birthdays (user_id, username, day, month, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            day = excluded.day,
            month = excluded.month,
            updated_at = excluded.updated_at
    """, (user_id, username, day, month, now, now))
    db.commit()


def get_all_birthdays():
    cursor.execute("""
        SELECT user_id, username, day, month
        FROM birthdays
        ORDER BY month, day
    """)
    return cursor.fetchall()


def get_birthday(user_id: int):
    cursor.execute(
        "SELECT user_id, username, day, month FROM birthdays WHERE user_id = ?",
        (user_id,)
    )
    return cursor.fetchone()


def birthdays_sorted_upcoming():
    items = []
    for r in get_all_birthdays():
        d, m = int(r["day"]), int(r["month"])
        items.append({
            "user_id": r["user_id"],
            "username": r["username"],
            "day": d,
            "month": m,
            "days_left": days_until_birthday(d, m),
            "next_date": next_birthday_date(d, m),
        })
    items.sort(key=lambda x: (x["days_left"], x["month"], x["day"]))
    return items


def construir_embed_lista_cumples_ephemeral() -> discord.Embed:
    hoy = today_local()
    items = birthdays_sorted_upcoming()
    embed = discord.Embed(
        title="🎂✨ LISTA DE CUMPLEAÑOS DEL TEAM ✨🎂",
        description=(
            f"📅 Hoy: **{hoy.day} de {MESES_ES[hoy.month]}**\n"
            f"Orden: del **más próximo** al más lejano\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.from_rgb(255, 105, 180),
        timestamp=utc_now()
    )
    if not items:
        embed.add_field(
            name="📭 Sin registros",
            value="Aún nadie registró su cumpleaños.\n¡Sé el primero con **Registrar mi cumpleaños**!",
            inline=False
        )
        return embed

    lineas = []
    for i, x in enumerate(items, start=1):
        if x["days_left"] == 0:
            badge = "🎉 **¡HOY!**"
        elif x["days_left"] == 1:
            badge = "⏰ **mañana**"
        elif x["days_left"] <= 7:
            badge = f"🔥 en **{x['days_left']}** días"
        elif x["days_left"] <= 30:
            badge = f"✨ en **{x['days_left']}** días"
        else:
            badge = f"en **{x['days_left']}** días"
        mes_e = MESES_EMOJI.get(x["month"], "📅")
        lineas.append(
            f"`#{i:02d}` {mes_e} **{x['day']:02d}/{x['month']:02d}** · "
            f"<@{x['user_id']}> — {badge}"
        )

    texto = "\n".join(lineas)
    if len(texto) <= 1024:
        embed.add_field(name="📋 Miembros", value=texto, inline=False)
    else:
        mid = len(lineas) // 2
        embed.add_field(name="📋 Miembros (1)", value="\n".join(lineas[:mid])[:1024], inline=False)
        embed.add_field(name="📋 Miembros (2)", value="\n".join(lineas[mid:])[:1024], inline=False)

    embed.set_footer(text=f"💕 {len(items)} registrados · Se borra en ~1 minuto")
    return embed


def construir_embed_panel_cumple_botones() -> discord.Embed:
    total = len(get_all_birthdays())
    hoy = today_local()
    items = birthdays_sorted_upcoming()
    hoy_n = sum(1 for x in items if x["days_left"] == 0)
    prox = next((x for x in items if x["days_left"] > 0), None)

    desc = (
        f"### 🗓️ Celebramos juntos en el team\n"
        f"Hoy es **{hoy.day} de {MESES_ES[hoy.month]} de {hoy.year}**\n\n"
        f"**1️⃣ Registrar mi cumpleaños**\n"
        f"　Solo **día y mes** (sin año). **Una sola vez** — no podrás cambiarlo tú.\n\n"
        f"**2️⃣ Ver calendario**\n"
        f"　Lista del más próximo al más lejano. Solo tú la ves ~1 minuto.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎂 Registrados: **{total}**"
    )
    if hoy_n:
        desc += f"\n🥳 Cumpleaños hoy: **{hoy_n}**"
    if prox:
        desc += (
            f"\n🔮 Próximo: <@{prox['user_id']}> "
            f"(`{prox['day']:02d}/{prox['month']:02d}` · {prox['days_left']} días)"
        )

    embed = discord.Embed(
        title="🎂✨ CUMPLEAÑOS — ENDCORE TEAM ✨🎂",
        description=desc,
        color=discord.Color.from_rgb(255, 20, 147),
        timestamp=utc_now()
    )
    embed.set_footer(text="Staff: !setcumple / !quitarcumple · Hora Chile")
    return embed


class BirthdayRegisterModal(discord.ui.Modal, title="Registrar mi cumpleaños"):
    fecha = discord.ui.TextInput(
        label="Día y mes (sin año)",
        placeholder="Ej: 15/03",
        max_length=10,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if get_birthday(interaction.user.id):
            await interaction.followup.send(
                "⚠️ Ya registraste tu cumpleaños y **no puedes cambiarlo** tú mismo.\n"
                "Si hay un error, pide a staff: `!setcumple @tú DD/MM`",
                ephemeral=True
            )
            return

        parsed = parse_cumple_ddmm(str(self.fecha.value))
        if not parsed:
            await interaction.followup.send(
                "❌ Fecha inválida. Usa solo **día/mes**, ejemplo: `15/03`",
                ephemeral=True
            )
            return

        day, month = parsed
        set_birthday(interaction.user.id, interaction.user.display_name, day, month)
        dias = days_until_birthday(day, month)
        if dias == 0:
            extra = "¡**Es hoy**! 🎉🥳"
        elif dias == 1:
            extra = "¡Es **mañana**! ⏰"
        else:
            extra = f"Faltan **{dias}** días ✨"

        await interaction.followup.send(
            f"🎂 ¡Listo! Registrado: **{day:02d} de {MESES_ES[month]}**\n{extra}\n\n"
            f"_No podrás modificarlo tú. Si falló, avisa a staff._",
            ephemeral=True
        )
        await actualizar_panel_cumpleanos()


class BirthdayPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar mi cumpleaños",
        style=discord.ButtonStyle.primary,
        emoji="🎂",
        custom_id="birthday_register_v1"
    )
    async def register_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if get_birthday(interaction.user.id):
            row = get_birthday(interaction.user.id)
            await interaction.response.send_message(
                f"⚠️ Ya tienes cumpleaños registrado: "
                f"**{int(row['day']):02d}/{int(row['month']):02d}**.\n"
                f"Solo se puede registrar **una vez**. Si está mal, pide a staff "
                f"`!setcumple @tú DD/MM`.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(BirthdayRegisterModal())

    @discord.ui.button(
        label="Ver calendario",
        style=discord.ButtonStyle.secondary,
        emoji="📅",
        custom_id="birthday_list_v1"
    )
    async def list_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construir_embed_lista_cumples_ephemeral()
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            msg = await interaction.original_response()
        except Exception:
            return

        async def _borrar():
            await asyncio.sleep(60)
            try:
                await msg.delete()
            except Exception:
                pass

        asyncio.create_task(_borrar())


async def actualizar_panel_cumpleanos():
    try:
        if not BIRTHDAY_CHANNEL_ID:
            return
        channel = bot.get_channel(BIRTHDAY_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró BIRTHDAY_CHANNEL_ID")
            return

        embed = construir_embed_panel_cumple_botones()
        msg = await obtener_mensaje_fijo(channel, "panel_cumple_botones")
        await msg.edit(content=None, embed=embed, view=BirthdayPanelView())
        print("✅ Panel de cumpleaños actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_panel_cumpleanos: {e}")


async def anunciar_cumpleanos_del_dia():
    try:
        if not BIRTHDAY_CHANNEL_ID:
            return
        channel = bot.get_channel(BIRTHDAY_CHANNEL_ID)
        if not channel:
            return

        hoy = today_local()
        key = f"birthday_announced_{hoy.isoformat()}"
        if get_progress_value(key, ""):
            return

        items = [x for x in birthdays_sorted_upcoming() if x["days_left"] == 0]
        if not items:
            set_progress_value(key, "none")
            return

        menciones = " ".join(f"<@{x['user_id']}>" for x in items)
        nombres = ", ".join(f"**{x['username']}**" for x in items)

        embed = discord.Embed(
            title="🎉🥳 ¡HOY HAY CUMPLEAÑOS EN EL TEAM! 🥳🎉",
            description=(
                f"### Hoy celebramos a {nombres}\n\n"
                f"🎈 Que tengas un día increíble\n"
                f"🎁 Gracias por ser parte de **Endcore Team**\n"
                f"💜 ¡Todos a felicitar en el chat!\n\n"
                f"{menciones}"
            ),
            color=discord.Color.from_rgb(255, 20, 147),
            timestamp=utc_now()
        )
        embed.set_footer(text="🎂 Felicidades del team completo")

        await channel.send(content=f"🎊 ¡Cumpleaños! {menciones}", embed=embed)
        set_progress_value(key, "sent")
        await actualizar_panel_cumpleanos()
        print(f"🎂 Anuncio de cumpleaños enviado: {nombres}")
    except Exception as e:
        print(f"❌ Error en anunciar_cumpleanos_del_dia: {e}")


# ===============================
# FUNCIONES DEL TEAM (Minero / Obrero / Constructor)
# ===============================
def get_user_functions(user_id: int) -> list[str]:
    cursor.execute(
        "SELECT funcion FROM member_functions WHERE user_id = ? ORDER BY funcion",
        (user_id,)
    )
    return [r["funcion"] for r in cursor.fetchall()]


def set_user_function(
    user_id: int,
    username: str,
    funcion: str,
    active: bool,
    lock_days: int | None = None,
):
    if active:
        locked_until = None
        if lock_days and lock_days > 0:
            locked_until = (
                utc_now() + timedelta(days=int(lock_days))
            ).isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO member_functions
            (user_id, username, funcion, assigned_at, locked_until)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, funcion, local_datetime_str(), locked_until))
    else:
        cursor.execute(
            "DELETE FROM member_functions WHERE user_id = ? AND funcion = ?",
            (user_id, funcion)
        )
    db.commit()


def get_function_lock_remaining(user_id: int, funcion: str) -> int:
    """Segundos restantes de bloqueo para no quitar el rol. 0 = libre."""
    cursor.execute(
        "SELECT locked_until FROM member_functions WHERE user_id = ? AND funcion = ?",
        (user_id, funcion)
    )
    row = cursor.fetchone()
    if not row:
        return 0
    try:
        locked = row["locked_until"]
    except (KeyError, IndexError, TypeError):
        return 0
    if not locked:
        return 0
    exp = parse_iso_datetime(locked)
    if not exp:
        return 0
    # asegurar comparable con utc_now()
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC_TZ)
    restante = int((exp - utc_now()).total_seconds())
    return max(0, restante)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def texto_dm_funcion_asignada(funcion_key: str) -> str:
    """Mensaje privado al elegir una función del team."""
    if funcion_key not in FUNCIONES_TEAM:
        return ""
    emoji, label, desc = (
        FUNCIONES_TEAM[funcion_key][1],
        FUNCIONES_TEAM[funcion_key][2],
        FUNCIONES_TEAM[funcion_key][3],
    )
    inicio = MINIMOS_ACTIVE_FROM.strftime("%d/%m/%Y")

    base = (
        f"📋 **Función asignada — Endcore Team**\n\n"
        f"Ahora eres **{emoji} {label}**\n"
        f"_{desc}_\n\n"
    )

    if funcion_key == "minero":
        base += (
            f"**Tu responsabilidad**\n"
            f"• Aportar End al team (con evidencia en el canal de End aportada)\n"
            f"• Mínimo semanal: **1 PV** = `{MIN_SHULKERS_MINERO_SEMANA}` shulkers **aportadas** "
            f"(solo cuenta lo que staff apruebe)\n\n"
            f"**Cómo cuenta el bot**\n"
            f"• El conteo de tu mínimo empieza **desde el momento en que elegiste este rol**\n"
            f"• Se revisa por **semana** (lunes a domingo, hora Chile)\n"
            f"• Los mínimos oficiales aplican a partir del **{inicio}**\n\n"
            f"**Candado del rol**\n"
            f"• Durante **{FUNCION_LOCK_DAYS} días** no puedes quitarte el rol tú mismo "
            f"(así nadie evade el mínimo)\n\n"
            f"**Si no cumples**\n"
            f"• Se te quita el rol y recibes advertencia por MD\n"
            f"• **Strike +1** (máximo {MAX_STRIKES_FUNCION})\n"
            f"• A la **{MAX_STRIKES_FUNCION}ª** semana sin cumplir → estado **restringido** "
            f"y el staff puede expulsarte del clan\n"
            f"• Si cumples una semana, tus strikes de ese rol vuelven a **0**\n\n"
            f"Revisa tu progreso con `!miminimo`\n"
            f"¡Gracias por aportar al team! ⛏️"
        )
    elif funcion_key == "obrero":
        base += (
            f"**Tu responsabilidad**\n"
            f"• Colocar End en las islas y registrarla en el bot\n"
            f"• Mínimo semanal: **1 PV** = `{MIN_SHULKERS_OBRERO_SEMANA}` shulkers **colocadas** "
            f"(Isla Principal + Secundaria; el Evento no cuenta para este mínimo)\n\n"
            f"**Cómo cuenta el bot**\n"
            f"• El conteo de tu mínimo empieza **desde el momento en que elegiste este rol**\n"
            f"• Se revisa por **semana** (lunes a domingo, hora Chile)\n"
            f"• Los mínimos oficiales aplican a partir del **{inicio}**\n\n"
            f"**Candado del rol**\n"
            f"• Durante **{FUNCION_LOCK_DAYS} días** no puedes quitarte el rol tú mismo "
            f"(así nadie evade el mínimo)\n\n"
            f"**Si no cumples**\n"
            f"• Se te quita el rol y recibes advertencia por MD\n"
            f"• **Strike +1** (máximo {MAX_STRIKES_FUNCION})\n"
            f"• A la **{MAX_STRIKES_FUNCION}ª** semana sin cumplir → estado **restringido** "
            f"y el staff puede expulsarte del clan\n"
            f"• Si cumples una semana, tus strikes de ese rol vuelven a **0**\n\n"
            f"Revisa tu progreso con `!miminimo`\n"
            f"¡Gracias por construir el team con End! 🧱"
        )
    else:  # constructor
        base += (
            f"**Tu responsabilidad**\n"
            f"• Construir y mejorar las islas del team\n"
            f"• Esta función la **supervisa el staff** (no hay mínimo automático de shulkers)\n\n"
            f"**Sobre el rol**\n"
            f"• Puedes quitarte Constructor cuando quieras\n"
            f"• El staff puede pedirte avances o tareas de build\n\n"
            f"¡Gracias por levantar las islas! 🏗️"
        )
    return base


def count_function(funcion: str) -> int:
    cursor.execute(
        "SELECT COUNT(*) AS c FROM member_functions WHERE funcion = ?",
        (funcion,)
    )
    row = cursor.fetchone()
    return int(row["c"] or 0)


def list_users_by_function(funcion: str):
    cursor.execute("""
        SELECT user_id, username, assigned_at
        FROM member_functions
        WHERE funcion = ?
        ORDER BY username COLLATE NOCASE
    """, (funcion,))
    return cursor.fetchall()


def week_id_from_date(d: date) -> str:
    """Semana ISO (lunes-domingo), ej: 2026-W32"""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def current_week_id() -> str:
    return week_id_from_date(today_local())


def previous_week_id() -> str:
    return week_id_from_date(today_local() - timedelta(days=7))


def week_date_bounds(week_id: str) -> tuple[str, str]:
    """Devuelve (fecha_inicio, fecha_fin) inclusive YYYY-MM-DD para un week_id ISO."""
    year_s, week_s = week_id.split("-W")
    year, week = int(year_s), int(week_s)
    start = date.fromisocalendar(year, week, 1)
    end = date.fromisocalendar(year, week, 7)
    return str(start), str(end)


def sum_end_aportada_periodo(user_id: int, fecha_ini: str, fecha_fin: str) -> int:
    cursor.execute("""
        SELECT COALESCE(SUM(total), 0) AS s
        FROM end_aportado
        WHERE user_id = ? AND fecha >= ? AND fecha <= ?
    """, (user_id, fecha_ini, fecha_fin))
    return int(cursor.fetchone()["s"] or 0)


def sum_end_colocada_periodo(user_id: int, fecha_ini: str, fecha_fin: str) -> int:
    """Principal + secundaria (no evento)."""
    total = 0
    for tabla in ("shulker", "shulker_secundaria"):
        cursor.execute(f"""
            SELECT COALESCE(SUM(total), 0) AS s
            FROM {tabla}
            WHERE user_id = ? AND fecha >= ? AND fecha <= ?
        """, (user_id, fecha_ini, fecha_fin))
        total += int(cursor.fetchone()["s"] or 0)
    return total


def get_strikes(user_id: int, funcion: str) -> int:
    cursor.execute(
        "SELECT strikes, last_fail_week FROM function_strikes WHERE user_id = ? AND funcion = ?",
        (user_id, funcion)
    )
    row = cursor.fetchone()
    return int(row["strikes"] or 0) if row else 0


def get_strike_row(user_id: int, funcion: str):
    cursor.execute(
        "SELECT strikes, last_fail_week, last_ok_week FROM function_strikes WHERE user_id = ? AND funcion = ?",
        (user_id, funcion)
    )
    return cursor.fetchone()


def set_strikes(user_id: int, funcion: str, strikes: int, fail_week: str | None = None, ok_week: str | None = None):
    cursor.execute(
        "SELECT strikes, last_fail_week, last_ok_week FROM function_strikes WHERE user_id = ? AND funcion = ?",
        (user_id, funcion)
    )
    row = cursor.fetchone()
    prev_fail = row["last_fail_week"] if row else None
    prev_ok = row["last_ok_week"] if row else None
    cursor.execute("""
        INSERT INTO function_strikes (user_id, funcion, strikes, last_fail_week, last_ok_week, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, funcion) DO UPDATE SET
            strikes = excluded.strikes,
            last_fail_week = COALESCE(excluded.last_fail_week, function_strikes.last_fail_week),
            last_ok_week = COALESCE(excluded.last_ok_week, function_strikes.last_ok_week),
            updated_at = excluded.updated_at
    """, (
        user_id,
        funcion,
        max(0, int(strikes)),
        fail_week if fail_week is not None else prev_fail,
        ok_week if ok_week is not None else prev_ok,
        local_datetime_str(),
    ))
    db.commit()


def is_member_restricted(user_id: int) -> bool:
    cursor.execute("SELECT status FROM member_status WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return bool(row and row["status"] == "restricted")


def set_member_restricted(user_id: int, username: str, reason: str):
    cursor.execute("""
        INSERT INTO member_status (user_id, username, status, restricted_at, reason)
        VALUES (?, ?, 'restricted', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            status = 'restricted',
            restricted_at = excluded.restricted_at,
            reason = excluded.reason
    """, (user_id, username, local_datetime_str(), reason))
    db.commit()


def clear_member_restricted(user_id: int):
    cursor.execute("""
        INSERT INTO member_status (user_id, status, restricted_at, reason)
        VALUES (?, 'active', NULL, NULL)
        ON CONFLICT(user_id) DO UPDATE SET
            status = 'active',
            restricted_at = NULL,
            reason = NULL
    """, (user_id,))
    db.commit()


def progreso_semana_funcion(user_id: int, funcion: str, week_id: str | None = None) -> tuple[int, int]:
    """Returns (actual_shulkers, minimo_shulkers) for that function/week."""
    wid = week_id or current_week_id()
    ini, fin = week_date_bounds(wid)
    if funcion == "minero":
        return sum_end_aportada_periodo(user_id, ini, fin), MIN_SHULKERS_MINERO_SEMANA
    if funcion == "obrero":
        return sum_end_colocada_periodo(user_id, ini, fin), MIN_SHULKERS_OBRERO_SEMANA
    return 0, 0


def construir_embed_panel_funciones() -> discord.Embed:
    lineas_count = []
    for key, (_, emoji, label, desc) in FUNCIONES_TEAM.items():
        n = count_function(key)
        lineas_count.append(f"{emoji} **{label}:** `{n}` miembros — _{desc}_")

    embed = discord.Embed(
        title="📋 FUNCIONES DEL TEAM — ENDCORE",
        description=(
            "### Elige tu(s) función(es) en el team\n"
            "Puedes tener **más de una**. Pulsa de nuevo para quitarla.\n\n"
            "⛏️ **Minero** — Farmea y **aporta End**\n"
            f"　Mínimo semanal: **1 PV** (`{MIN_SHULKERS_MINERO_SEMANA}` shulkers aportadas)\n"
            "🧱 **Obrero** — **Coloca End** en las islas\n"
            f"　Mínimo semanal: **1 PV** (`{MIN_SHULKERS_OBRERO_SEMANA}` shulkers colocadas)\n"
            "🏗️ **Constructor** — **Construye** en las islas\n"
            "　Supervisión del **staff** (sin mínimo automático)\n\n"
            f"⚠️ Si no cumples el mínimo, se te **quita el rol** y recibes aviso por MD.\n"
            f"A la **{MAX_STRIKES_FUNCION}ª** semana sin cumplir → usuario **restringido**.\n\n"
            f"🔒 **Minero/Obrero:** al elegir el rol queda **bloqueado {FUNCION_LOCK_DAYS} días** "
            f"(no puedes quitártelo). Así nadie evade el mínimo.\n"
            f"🏗️ Constructor se puede quitar libremente (lo supervisa staff).\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(lineas_count)
        ),
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=utc_now()
    )
    embed.set_footer(text="Multi-rol · !miminimo · Staff: !funciones · Hora Chile")
    return embed


class FuncionesPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _toggle(
        self,
        interaction: discord.Interaction,
        funcion_key: str,
    ):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Solo se puede usar dentro del servidor.",
                ephemeral=True
            )
            return

        if funcion_key not in FUNCIONES_TEAM:
            await interaction.response.send_message("❌ Función inválida.", ephemeral=True)
            return

        role_id, emoji, label, desc = FUNCIONES_TEAM[funcion_key]
        role = interaction.guild.get_role(role_id) if interaction.guild else None
        if role is None:
            await interaction.response.send_message(
                f"❌ No se encontró el rol de **{label}** en el servidor.\n"
                f"Revisa que el bot pueda verlo y que el ID sea correcto.",
                ephemeral=True
            )
            return

        member = interaction.user
        tiene = role in member.roles
        await interaction.response.defer(ephemeral=True)

        if is_member_restricted(member.id) and not tiene:
            await interaction.followup.send(
                "🚫 Estás **restringido** por incumplir mínimos varias semanas.\n"
                "No puedes tomar funciones hasta que staff revise tu caso "
                "(`!quitarrestriccion` solo staff).",
                ephemeral=True
            )
            return

        try:
            if tiene:
                # Candado anti-evasión: Minero/Obrero no se pueden quitar durante N días
                if funcion_key in ("minero", "obrero"):
                    restante = get_function_lock_remaining(member.id, funcion_key)
                    if restante > 0:
                        await interaction.followup.send(
                            f"🔒 No puedes quitar **{emoji} {label}** todavía.\n"
                            f"Al elegirlo queda bloqueado **{FUNCION_LOCK_DAYS} días** "
                            f"para que no se evada el mínimo semanal.\n"
                            f"Tiempo restante: **{format_duration(restante)}**.\n"
                            f"_Si hay un error grave, pide a staff `!forzarquitarfuncion`._",
                            ephemeral=True
                        )
                        return

                await member.remove_roles(role, reason="Función del team desactivada")
                set_user_function(member.id, member.display_name, funcion_key, False)
                msg = f"➖ Quitaste **{emoji} {label}**.\n_{desc}_"
            else:
                # Si falló esta semana, solo puede retomar el rol cumpliendo el mínimo
                if funcion_key in ("minero", "obrero"):
                    row = get_strike_row(member.id, funcion_key)
                    last_fail = row["last_fail_week"] if row else None
                    if last_fail == current_week_id():
                        actual, minimo = progreso_semana_funcion(member.id, funcion_key)
                        if actual < minimo:
                            pv_a, sh_a = shulkers_a_pv_y_shulkers(actual)
                            pv_m, sh_m = shulkers_a_pv_y_shulkers(minimo)
                            tipo = "aportada" if funcion_key == "minero" else "colocada"
                            await interaction.followup.send(
                                f"⚠️ Esta semana te quitaron **{label}** por no cumplir el mínimo.\n"
                                f"Para volver a tomarlo debes alcanzar **1 PV** de End **{tipo}** esta semana.\n"
                                f"Progreso: `{actual}` shulkers (`{pv_a}` PVs + `{sh_a}`) / "
                                f"mínimo `{minimo}` (`{pv_m}` PVs).",
                                ephemeral=True
                            )
                            return

                lock_days = FUNCION_LOCK_DAYS if funcion_key in ("minero", "obrero") else None
                await member.add_roles(role, reason="Función del team activada")
                set_user_function(
                    member.id,
                    member.display_name,
                    funcion_key,
                    True,
                    lock_days=lock_days,
                )
                msg = f"➕ Ahora eres **{emoji} {label}**.\n_{desc}_"
                if funcion_key == "minero":
                    msg += (
                        f"\n📊 Mínimo semanal: **1 PV** aportada "
                        f"(`{MIN_SHULKERS_MINERO_SEMANA}` shulkers)."
                        f"\n🔒 Rol bloqueado **{FUNCION_LOCK_DAYS} días** (no puedes quitártelo)."
                    )
                elif funcion_key == "obrero":
                    msg += (
                        f"\n📊 Mínimo semanal: **1 PV** colocada "
                        f"(`{MIN_SHULKERS_OBRERO_SEMANA}` shulkers)."
                        f"\n🔒 Rol bloqueado **{FUNCION_LOCK_DAYS} días** (no puedes quitártelo)."
                    )
                elif funcion_key == "constructor":
                    msg += "\n👀 Esta función la supervisa el **staff** (puedes quitarla cuando quieras)."

                # MD con reglas completas al asignar
                try:
                    await member.send(texto_dm_funcion_asignada(funcion_key))
                    msg += "\n📩 Te envié la explicación completa por **privado**."
                except Exception:
                    msg += (
                        "\n⚠️ No pude enviarte MD (¿tienes mensajes directos cerrados?). "
                        "Abre DMs del server para recibir las reglas del rol."
                    )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ No pude cambiar tu rol. El bot necesita permiso **Gestionar roles** "
                "y su rol debe estar **por encima** de Minero/Obrero/Constructor.",
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error al actualizar rol: `{e}`",
                ephemeral=True
            )
            return

        activas = get_user_functions(member.id)
        if activas:
            nombres = []
            for k in activas:
                e, lab = FUNCIONES_TEAM[k][1], FUNCIONES_TEAM[k][2]
                nombres.append(f"{e} {lab}")
            msg += f"\n\n📋 Tus funciones: **{', '.join(nombres)}**"
        else:
            msg += "\n\n📋 Ahora no tienes ninguna función asignada."

        await interaction.followup.send(msg, ephemeral=True)
        await actualizar_panel_funciones()

    @discord.ui.button(
        label="Minero",
        style=discord.ButtonStyle.primary,
        emoji="⛏️",
        custom_id="func_team_minero_v1"
    )
    async def btn_minero(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "minero")

    @discord.ui.button(
        label="Obrero",
        style=discord.ButtonStyle.primary,
        emoji="🧱",
        custom_id="func_team_obrero_v1"
    )
    async def btn_obrero(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "obrero")

    @discord.ui.button(
        label="Constructor",
        style=discord.ButtonStyle.primary,
        emoji="🏗️",
        custom_id="func_team_constructor_v1"
    )
    async def btn_constructor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "constructor")


async def actualizar_panel_funciones():
    try:
        if not FUNCIONES_CHANNEL_ID:
            return
        channel = bot.get_channel(FUNCIONES_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró FUNCIONES_CHANNEL_ID")
            return
        embed = construir_embed_panel_funciones()
        msg = await obtener_mensaje_fijo(channel, "panel_funciones_team")
        await msg.edit(content=None, embed=embed, view=FuncionesPanelView())
        print("✅ Panel de funciones actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_panel_funciones: {e}")


async def _dm_usuario(user_id: int, content: str):
    try:
        user = await bot.fetch_user(user_id)
        await user.send(content)
    except Exception:
        pass


async def evaluar_minimos_semana_anterior():
    """
    Corre 1 vez por semana (lunes en adelante): revisa la semana ISO anterior.
    Minero → End aportada | Obrero → End colocada.
    """
    try:
        semana = previous_week_id()
        flag_key = f"minimos_evaluados_{semana}"
        if get_progress_value(flag_key, ""):
            return

        # Solo evaluar cuando ya empezó la semana siguiente (lunes+)
        if today_local().isoweekday() == 7:
            # domingo: aún no cerramos la semana actual
            return

        ini, fin = week_date_bounds(semana)
        # No evaluar semanas anteriores al lanzamiento oficial de mínimos
        if date.fromisoformat(fin) < MINIMOS_ACTIVE_FROM:
            set_progress_value(flag_key, f"skipped_before_{MINIMOS_ACTIVE_FROM}")
            print(f"⏭️ Semana {semana} omitida (antes de MINIMOS_ACTIVE_FROM={MINIMOS_ACTIVE_FROM})")
            return

        print(f"🔎 Evaluando mínimos de la semana {semana} ({ini} → {fin})")

        guild = None
        for g in bot.guilds:
            guild = g
            break

        # Usuarios a revisar: quienes tenían la función en DB
        for funcion in ("minero", "obrero"):
            rows = list_users_by_function(funcion)
            role_id = FUNCIONES_TEAM[funcion][0]
            emoji, label = FUNCIONES_TEAM[funcion][1], FUNCIONES_TEAM[funcion][2]
            minimo = MIN_SHULKERS_MINERO_SEMANA if funcion == "minero" else MIN_SHULKERS_OBRERO_SEMANA
            tipo = "aportada" if funcion == "minero" else "colocada"

            for r in rows:
                uid = int(r["user_id"])
                uname = r["username"]
                if is_member_restricted(uid):
                    continue

                actual = (
                    sum_end_aportada_periodo(uid, ini, fin)
                    if funcion == "minero"
                    else sum_end_colocada_periodo(uid, ini, fin)
                )

                if actual >= minimo:
                    set_strikes(uid, funcion, 0, ok_week=semana)
                    continue

                # Incumplimiento
                strikes = get_strikes(uid, funcion) + 1
                set_strikes(uid, funcion, strikes, fail_week=semana)

                # Quitar rol + DB
                set_user_function(uid, uname, funcion, False)
                if guild:
                    member = guild.get_member(uid)
                    role = guild.get_role(role_id)
                    if member and role and role in member.roles:
                        try:
                            await member.remove_roles(
                                role,
                                reason=f"No cumplió mínimo semanal {funcion} ({semana})"
                            )
                        except Exception:
                            pass

                pv_a, sh_a = shulkers_a_pv_y_shulkers(actual)
                if strikes >= MAX_STRIKES_FUNCION:
                    set_member_restricted(
                        uid, uname,
                        f"{strikes} semanas sin mínimo de {label} (última: {semana})"
                    )
                    # Quitar todas las funciones
                    for fk in list(FUNCIONES_TEAM.keys()):
                        set_user_function(uid, uname, fk, False)
                        rid = FUNCIONES_TEAM[fk][0]
                        if guild:
                            mem = guild.get_member(uid)
                            rol = guild.get_role(rid)
                            if mem and rol and rol in mem.roles:
                                try:
                                    await mem.remove_roles(rol, reason="Usuario restringido")
                                except Exception:
                                    pass
                    if ROLE_RESTRINGIDO_ID and guild:
                        mem = guild.get_member(uid)
                        rol_r = guild.get_role(ROLE_RESTRINGIDO_ID)
                        if mem and rol_r:
                            try:
                                await mem.add_roles(rol_r, reason="Restringido por mínimos")
                            except Exception:
                                pass

                    await _dm_usuario(
                        uid,
                        f"🚫 **Aviso grave — Endcore Team**\n\n"
                        f"No cumpliste el mínimo de **{emoji} {label}** "
                        f"({tipo}: `{actual}` shulkers / `{pv_a}` PVs) en la semana `{semana}`.\n"
                        f"Es la **{strikes}ª** semana sin cumplir.\n\n"
                        f"Has pasado a estado **RESTRINGIDO**.\n"
                        f"Se te quitaron las funciones del team.\n"
                        f"Staff revisará si continúas en el clan.\n"
                        f"Si crees que es un error, habla con un admin."
                    )
                else:
                    await _dm_usuario(
                        uid,
                        f"⚠️ **Advertencia — Endcore Team**\n\n"
                        f"No cumpliste el mínimo semanal de **{emoji} {label}**.\n"
                        f"Semana: `{semana}` ({ini} → {fin})\n"
                        f"Registrado: `{actual}` shulkers (`{pv_a}` PVs + `{sh_a}`) de End **{tipo}**\n"
                        f"Mínimo: **1 PV** (`{minimo}` shulkers)\n\n"
                        f"Se te **quitó el rol** de {label}.\n"
                        f"Puedes volver a tomarlo cuando cumplas el mínimo de **esta** semana.\n\n"
                        f"Strike: **{strikes}/{MAX_STRIKES_FUNCION}**\n"
                        f"A la **{MAX_STRIKES_FUNCION}ª** semana sin cumplir quedarás **restringido** "
                        f"y podrías ser expulsado del clan."
                    )

                print(f"⚠️ Mínimo fallido: {uname} ({uid}) {funcion} strikes={strikes} actual={actual}")

        set_progress_value(flag_key, local_datetime_str())
        await actualizar_panel_funciones()
        print(f"✅ Evaluación de mínimos {semana} terminada")
    except Exception as e:
        print(f"❌ Error en evaluar_minimos_semana_anterior: {e}")


async def obtener_mensaje_fijo(channel: discord.TextChannel, tipo: str):
    cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    row = cursor.fetchone()

    if row:
        try:
            return await channel.fetch_message(row["message_id"])
        except Exception as e:
            print(f"⚠️ No se pudo recuperar mensaje fijo {tipo}: {e}")

    msg = await channel.send("Cargando...")
    cursor.execute(
        "INSERT OR REPLACE INTO mensajes_fijos (tipo, message_id) VALUES (?, ?)",
        (tipo, msg.id)
    )
    db.commit()
    return msg

async def eliminar_mensaje_fijo_si_existe(channel: discord.TextChannel, tipo: str):
    cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    row = cursor.fetchone()

    if row and row["message_id"]:
        try:
            msg = await channel.fetch_message(row["message_id"])
            await msg.delete()
            print(f"🗑️ Mensaje fijo eliminado: {tipo}")
        except Exception as e:
            print(f"⚠️ No se pudo eliminar mensaje fijo {tipo}: {e}")

    cursor.execute("DELETE FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    db.commit()


async def recrear_mensajes_fijos_ordenados(channel: discord.TextChannel, tipos: list[str]):
    existentes = []

    for tipo in tipos:
        cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
        row = cursor.fetchone()
        msg = None
        if row:
            try:
                msg = await channel.fetch_message(row["message_id"])
            except Exception:
                msg = None
        existentes.append((tipo, msg))

    ids_existentes = [msg.id for _, msg in existentes if msg is not None]
    orden_correcto = len(ids_existentes) == len(tipos) and ids_existentes == sorted(ids_existentes)

    if orden_correcto:
        return {tipo: msg for tipo, msg in existentes if msg is not None}

    print(f"🔁 Reordenando mensajes fijos en {channel.name}: {', '.join(tipos)}")

    for tipo, msg in existentes:
        if msg is not None:
            try:
                await msg.delete()
            except Exception:
                pass
        cursor.execute("DELETE FROM mensajes_fijos WHERE tipo = ?", (tipo,))

    db.commit()

    nuevos = {}
    for tipo in tipos:
        nuevo = await channel.send("Cargando...")
        cursor.execute(
            "INSERT OR REPLACE INTO mensajes_fijos (tipo, message_id) VALUES (?, ?)",
            (tipo, nuevo.id)
        )
        nuevos[tipo] = nuevo

    db.commit()
    return nuevos

async def limpiar_mensajes_duplicados_por_titulo(
    channel: discord.TextChannel,
    titulos_objetivo: list[str],
    keep_message_ids: list[int] | None = None,
    limite: int = 100
):
    titulos_normalizados = {t.strip().lower() for t in titulos_objetivo if t}
    keep_ids = set(keep_message_ids or [])

    candidatos = []
    async for msg in channel.history(limit=limite):
        if msg.author != bot.user or not msg.embeds:
            continue

        titulo = (msg.embeds[0].title or '').strip()
        if titulo.lower() in titulos_normalizados:
            candidatos.append(msg)

    grupos = {}
    for msg in candidatos:
        titulo = (msg.embeds[0].title or '').strip().lower()
        grupos.setdefault(titulo, []).append(msg)

    for _, mensajes in grupos.items():
        if len(mensajes) <= 1:
            continue

        mensajes.sort(key=lambda m: m.created_at)

        mensaje_a_conservar = None
        for msg in mensajes:
            if msg.id in keep_ids:
                mensaje_a_conservar = msg
                break

        if mensaje_a_conservar is None:
            mensaje_a_conservar = mensajes[-1]

        for msg in mensajes:
            if msg.id == mensaje_a_conservar.id:
                continue
            try:
                await msg.delete()
                print(f"🧹 Duplicado eliminado en {channel.name}: {msg.id}")
            except Exception as e:
                print(f"⚠️ No se pudo eliminar duplicado {msg.id}: {e}")

def is_staff_member(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or member.guild_permissions.manage_messages
    )

def get_staff_action_info(action: str) -> tuple[str, str]:
    mapping = {
        "end_request_created": (
            "📥 Nueva solicitud de End aportada",
            "El usuario envió cantidad + evidencia y quedó pendiente de revisión del staff."
        ),
        "end_request_approved": (
            "✅ Solicitud aprobada",
            "La evidencia fue aceptada y la cantidad ya fue sumada al ranking público de End aportada."
        ),
        "end_request_rejected": (
            "❌ Solicitud rechazada",
            "La evidencia fue revisada y rechazada por el staff. No se sumó al ranking público."
        ),
    }
    return mapping.get(
        action,
        ("🛡 Acción de staff", "Se registró una acción administrativa.")
    )

# ===============================
# COOLDOWNS PERSISTENTES
# ===============================
def cleanup_expired_cooldowns():
    cursor.execute(
        "DELETE FROM user_cooldowns WHERE expires_at <= ?",
        (iso_utc_now(),)
    )
    db.commit()

def set_cooldown(feature: str, user_id: int, seconds: int):
    expires_at = (utc_now().timestamp() + seconds)
    expires_iso = datetime.fromtimestamp(expires_at, tz=UTC_TZ).isoformat()
    cursor.execute("""
        INSERT OR REPLACE INTO user_cooldowns (feature, user_id, expires_at)
        VALUES (?, ?, ?)
    """, (feature, user_id, expires_iso))
    db.commit()

def get_cooldown_remaining(feature: str, user_id: int) -> int:
    cleanup_expired_cooldowns()
    cursor.execute("""
        SELECT expires_at
        FROM user_cooldowns
        WHERE feature = ? AND user_id = ?
    """, (feature, user_id))
    row = cursor.fetchone()
    if not row:
        return 0

    expires_dt = parse_iso_datetime(row["expires_at"])
    if not expires_dt:
        cursor.execute("DELETE FROM user_cooldowns WHERE feature = ? AND user_id = ?", (feature, user_id))
        db.commit()
        return 0

    restante = int((expires_dt - utc_now()).total_seconds())
    if restante <= 0:
        cursor.execute("DELETE FROM user_cooldowns WHERE feature = ? AND user_id = ?", (feature, user_id))
        db.commit()
        return 0

    return restante

# ===============================
# PENDIENTES DE IMAGEN PERSISTENTES
# ===============================
def cleanup_expired_pending_end():
    cursor.execute(
        "DELETE FROM pending_end_uploads WHERE expires_at <= ?",
        (iso_utc_now(),)
    )
    db.commit()

def save_pending_end(
    user_id: int,
    username: str,
    fecha: str,
    cantidad: int,
    channel_id: int,
    ubicacion: str = "",
):
    now = utc_now()
    expires = datetime.fromtimestamp(
        now.timestamp() + END_UPLOAD_TIMEOUT_SECONDS,
        tz=UTC_TZ
    )

    cursor.execute("""
        INSERT OR REPLACE INTO pending_end_uploads
        (user_id, username, fecha, cantidad, channel_id, created_at, expires_at, ubicacion)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        username,
        fecha,
        cantidad,
        channel_id,
        now.isoformat(),
        expires.isoformat(),
        (ubicacion or "").strip()[:500],
    ))
    db.commit()

def clear_pending_end(user_id: int):
    cursor.execute("DELETE FROM pending_end_uploads WHERE user_id = ?", (user_id,))
    db.commit()

def get_pending_end(user_id: int):
    cleanup_expired_pending_end()
    cursor.execute("""
        SELECT user_id, username, fecha, cantidad, channel_id, created_at, expires_at, ubicacion
        FROM pending_end_uploads
        WHERE user_id = ?
    """, (user_id,))
    row = cursor.fetchone()
    return row

def get_pending_end_remaining_seconds(user_id: int) -> int:
    row = get_pending_end(user_id)
    if not row:
        return 0

    expires_dt = parse_iso_datetime(row["expires_at"])
    if not expires_dt:
        clear_pending_end(user_id)
        return 0

    restante = int((expires_dt - utc_now()).total_seconds())
    if restante <= 0:
        clear_pending_end(user_id)
        return 0
    return restante

# ===============================
# REQUESTS / LOGS
# ===============================
def get_request_by_review_message_id(message_id: int):
    cursor.execute("""
        SELECT id, user_id, username, fecha, cantidad, image_url, status,
               rejection_reason, review_message_id, reviewed_by, reviewed_at, created_at,
               ubicacion
        FROM end_requests
        WHERE review_message_id = ?
    """, (message_id,))
    return cursor.fetchone()

def get_request_by_id(request_id: int):
    cursor.execute("""
        SELECT id, user_id, username, fecha, cantidad, image_url, status,
               rejection_reason, review_message_id, reviewed_by, reviewed_at, created_at,
               ubicacion
        FROM end_requests
        WHERE id = ?
    """, (request_id,))
    return cursor.fetchone()

async def log_staff_action(
    action: str,
    request_id: int | None,
    target_user_id: int | None,
    target_username: str | None,
    actor_user_id: int | None,
    actor_username: str | None,
    amount: int | None,
    reason: str | None = None
):
    created_at = iso_utc_now()

    cursor.execute("""
        INSERT INTO staff_logs (
            action, request_id, target_user_id, target_username,
            actor_user_id, actor_username, amount, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        action,
        request_id,
        target_user_id,
        target_username,
        actor_user_id,
        actor_username,
        amount,
        reason,
        created_at
    ))
    db.commit()

    if STAFF_LOG_CHANNEL_ID:
        try:
            ch = bot.get_channel(STAFF_LOG_CHANNEL_ID)
            if ch:
                color = discord.Color.blurple()
                if action == "end_request_approved":
                    color = discord.Color.green()
                elif action == "end_request_rejected":
                    color = discord.Color.red()
                elif action == "end_request_created":
                    color = discord.Color.orange()

                action_title, action_desc = get_staff_action_info(action)

                embed = discord.Embed(
                    title="🛡 Staff Log",
                    description=action_desc,
                    color=color,
                    timestamp=utc_now()
                )

                embed.add_field(name="Acción", value=action_title, inline=False)

                if request_id:
                    embed.add_field(name="Solicitud", value=f"`#{request_id}`", inline=True)
                if target_user_id:
                    embed.add_field(name="Usuario", value=f"<@{target_user_id}>", inline=True)
                if amount is not None:
                    embed.add_field(name="Cantidad", value=f"`{amount}` end aportada", inline=True)
                if actor_user_id:
                    embed.add_field(name="Realizado por", value=f"<@{actor_user_id}>", inline=True)
                if reason:
                    embed.add_field(name="Motivo", value=reason[:1024], inline=False)

                await ch.send(embed=embed)
        except Exception as e:
            print(f"⚠️ No se pudo enviar log al canal staff: {e}")

def construir_embed_revision(request_row, reviewer_name: str | None = None):
    request_id = request_row["id"]
    user_id = request_row["user_id"]
    cantidad = request_row["cantidad"]
    fecha_registro = request_row["fecha"]
    image_url = request_row["image_url"]
    status = request_row["status"]
    rejection_reason = request_row["rejection_reason"]
    reviewed_by = request_row["reviewed_by"]
    reviewed_at = request_row["reviewed_at"]
    created_at = request_row["created_at"]

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
        timestamp=utc_now()
    )

    # ubicacion puede no existir en filas muy antiguas
    try:
        ubicacion = (request_row["ubicacion"] or "").strip()
    except (KeyError, IndexError, TypeError):
        ubicacion = ""

    embed.add_field(name="👤 Usuario", value=f"<@{user_id}>", inline=True)
    embed.add_field(name="📦 Cantidad", value=f"`{cantidad}` end aportada", inline=True)
    embed.add_field(name="📅 Fecha", value=f"`{fecha_registro}`", inline=True)
    if ubicacion:
        embed.add_field(
            name="📍 Dónde la dejó",
            value=ubicacion[:1024],
            inline=False
        )
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

    if status == "rejected" and rejection_reason:
        embed.add_field(name="📝 Motivo de rechazo", value=rejection_reason[:1024], inline=False)

    embed.set_image(url=image_url)
    embed.set_footer(text=f"Solicitud #{request_id}")
    return embed

# ===============================
# PANEL PROGRESO (ISLA PRINCIPAL + SECUNDARIA)
# ===============================
def asegurar_base_progreso_si_falta():
    if not get_progress_value("base_level", ""):
        set_progress_value("base_level", str(DEFAULT_BASE_LEVEL))
        set_progress_value("base_shulkers", str(total_shulkers_all_time()))
        set_progress_value("base_date", local_date_str())
        print(f"✅ Base progreso Principal creada: {DEFAULT_BASE_LEVEL:,}")

    if not get_progress_value("base_level_secundaria", ""):
        set_progress_value("base_level_secundaria", str(DEFAULT_BASE_LEVEL_SECUNDARIA))
        set_progress_value("base_shulkers_secundaria", str(total_secundaria_all_time()))
        set_progress_value("base_date_secundaria", local_date_str())
        print(f"✅ Base progreso Secundaria creada: {DEFAULT_BASE_LEVEL_SECUNDARIA:,}")


def _calcular_progreso_isla(base_level: int, base_shulkers: int, total_sh: int, hoy_sh: int):
    nuevos_sh = max(0, total_sh - base_shulkers)
    niveles_ganados = nuevos_sh * LEVELS_PER_SHULKER
    nivel_estimado = base_level + niveles_ganados
    faltan_meta = max(0, TARGET_NEXT_LEVEL - nivel_estimado)
    shulkers_meta = (faltan_meta + LEVELS_PER_SHULKER - 1) // LEVELS_PER_SHULKER if LEVELS_PER_SHULKER > 0 else 0
    pv_meta, sh_meta = shulkers_a_pv_y_shulkers(shulkers_meta)
    faltan_diario = max(0, DAILY_SHULKER_GOAL - hoy_sh)
    pct_meta = (nivel_estimado / TARGET_NEXT_LEVEL) * 100 if TARGET_NEXT_LEVEL else 0
    pct_dia = (hoy_sh / DAILY_SHULKER_GOAL) * 100 if DAILY_SHULKER_GOAL else 0
    return {
        "nuevos_sh": nuevos_sh,
        "niveles_ganados": niveles_ganados,
        "nivel_estimado": nivel_estimado,
        "pv_meta": pv_meta,
        "sh_meta": sh_meta,
        "faltan_diario": faltan_diario,
        "pct_meta": pct_meta,
        "pct_dia": pct_dia,
        "bar_meta": barra_meta(nivel_estimado, TARGET_NEXT_LEVEL, largo=20),
        "bar_dia": barra_meta(hoy_sh, DAILY_SHULKER_GOAL, largo=20),
        "hoy_sh": hoy_sh,
    }


def _embed_progreso_isla(
    titulo: str,
    color: discord.Color,
    base_level: int,
    base_date: str,
    datos: dict,
):
    embed = discord.Embed(
        title=titulo,
        color=color,
        timestamp=utc_now()
    )
    embed.add_field(
        name="🔹 NIVEL ACTUAL",
        value=f"**{datos['nivel_estimado']:,}**",
        inline=False
    )
    embed.add_field(
        name="📦 META DIARIA",
        value=(
            f"`{datos['hoy_sh']:,} / {DAILY_SHULKER_GOAL:,} shulkers`\n"
            f"`{datos['bar_dia']}` `{datos['pct_dia']:.1f}%`\n"
            f"Faltan: `{datos['faltan_diario']:,}`"
        ),
        inline=False
    )
    embed.add_field(
        name="🏆 PRÓXIMA META GLOBAL",
        value=(
            f"`{datos['nivel_estimado']:,} / {TARGET_NEXT_LEVEL:,}`\n"
            f"`{datos['bar_meta']}` `{datos['pct_meta']:.1f}%`\n"
            f"Faltan: `{datos['pv_meta']}` PVS + `{datos['sh_meta']}` SHULKERS"
        ),
        inline=False
    )
    embed.set_footer(
        text=f"Base exacta: {base_level:,} | Desde: {base_date or 'sin calibrar'} | Hora Chile"
    )
    return embed


async def actualizar_panel_progreso():
    try:
        if not PROGRESS_CHANNEL_ID:
            return

        channel = bot.get_channel(PROGRESS_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró PROGRESS_CHANNEL_ID")
            return

        # —— Isla Principal ——
        base_level_p = int(get_progress_value("base_level", "0") or 0)
        base_shulkers_p = int(get_progress_value("base_shulkers", "0") or 0)
        base_date_p = get_progress_value("base_date", "")
        datos_p = _calcular_progreso_isla(
            base_level_p,
            base_shulkers_p,
            total_shulkers_all_time(),
            total_shulkers_today(),
        )
        embed_p = _embed_progreso_isla(
            "🏝️ ISLA PRINCIPAL — PROGRESO",
            discord.Color.dark_teal(),
            base_level_p,
            base_date_p,
            datos_p,
        )

        # —— Isla Secundaria ——
        base_level_s = int(get_progress_value("base_level_secundaria", "0") or 0)
        base_shulkers_s = int(get_progress_value("base_shulkers_secundaria", "0") or 0)
        base_date_s = get_progress_value("base_date_secundaria", "")
        datos_s = _calcular_progreso_isla(
            base_level_s,
            base_shulkers_s,
            total_secundaria_all_time(),
            total_secundaria_today(),
        )
        embed_s = _embed_progreso_isla(
            "🌿 ISLA SECUNDARIA — PROGRESO",
            discord.Color.teal(),
            base_level_s,
            base_date_s,
            datos_s,
        )

        mensajes = await recrear_mensajes_fijos_ordenados(channel, [
            "panel_progreso_principal",
            "panel_progreso_secundaria",
        ])
        await mensajes["panel_progreso_principal"].edit(content=None, embed=embed_p)
        await mensajes["panel_progreso_secundaria"].edit(content=None, embed=embed_s)

        # Limpia el panel viejo de una sola isla si existía
        await eliminar_mensaje_fijo_si_existe(channel, "panel_progreso")

        await limpiar_mensajes_duplicados_por_titulo(
            channel,
            [embed_p.title, embed_s.title],
            keep_message_ids=[
                mensajes["panel_progreso_principal"].id,
                mensajes["panel_progreso_secundaria"].id,
            ],
        )
        print("✅ Paneles de progreso (Principal + Secundaria) actualizados")
    except Exception as e:
        print(f"❌ Error en actualizar_panel_progreso: {e}")

# ===============================
# RANKINGS
# ===============================
async def crear_embed_ranking(
    titulo,
    emoji,
    color,
    datos,
    footer,
    total_periodo_shulkers: int,
    mostrar_equivalencias: bool = True,
    unidad: str = "shulkers"
):
    if not datos:
        ranking_text = "_Sin registros aún_"
        maximo = 0
    else:
        maximo = int(datos[0]["s"] or 0)
        lines = []

        for i, row in enumerate(datos, start=1):
            user = cortar_nombre(row["username"], 20)
            total = int(row["s"] or 0)

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
                f"{total} {unidad} • {porcentaje}%\n"
                f"{bar}"
            )

        ranking_text = "\n\n".join(lines)

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=ranking_text,
        color=color,
        timestamp=utc_now()
    )

    if mostrar_equivalencias:
        stacks, niveles, pv, resto = equivalencias(total_periodo_shulkers)

        resumen = (
            f"`{format_number(total_periodo_shulkers)}` SHULKERS • "
            f"`{format_number(niveles)}` NIVELES • "
            f"`{pv}` PVS"
        )

        embed.add_field(name="📊 RESUMEN", value=resumen, inline=False)
        embed.add_field(name="📦 TOTAL", value=f"`{format_number(total_periodo_shulkers)}` SHULKERS", inline=True)
        embed.add_field(name="⚖ EQUIVALENTE", value=f"`{pv}` PVS + `{resto}` SHULKERS", inline=True)
    else:
        embed.add_field(
            name="📦 TOTAL",
            value=f"`{format_number(total_periodo_shulkers)}` {unidad.upper()}",
            inline=False
        )

    embed.set_footer(text=f"{footer} | Hora Chile")
    return embed

async def crear_embed_ranking_mes_pasado_gamer(
    titulo,
    emoji,
    color,
    datos,
    footer,
    total_periodo_valor: int,
    mostrar_equivalencias: bool = True,
    unidad: str = "shulkers"
):
    if not datos:
        descripcion = (
            "💤 **Temporada cerrada**\n"
            "Nadie dejó marca en este periodo.\n"
            "El próximo mes puede ser tuyo."
        )
    else:
        top1 = cortar_nombre(datos[0]["username"], 20)
        top1_total = int(datos[0]["s"] or 0)
        lineas = [
            f"👑 **MVP del mes pasado:** **{top1}** con **{format_number(top1_total)} {unidad}**",
            "",
            "⚔️ **Tabla de guerra:**"
        ]

        maximo = int(datos[0]["s"] or 0)
        iconos = ["👑", "🔥", "⚡", "🎯", "🛡️"]

        for i, row in enumerate(datos, start=1):
            user = cortar_nombre(row["username"], 20)
            total = int(row["s"] or 0)
            porcentaje = int(round((total / maximo) * 100)) if maximo > 0 else 0
            barra = barra_progreso(total, maximo, largo=10)
            icono = iconos[i - 1] if i <= len(iconos) else "✨"
            lineas.append(
                f"{icono} **#{i} {user}** — **{format_number(total)} {unidad}**\n"
                f"`Poder: {porcentaje}%` {barra}"
            )

        descripcion = "\n\n".join(lineas)

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=descripcion,
        color=color,
        timestamp=utc_now()
    )

    if mostrar_equivalencias:
        stacks, niveles, pv, resto = equivalencias(total_periodo_valor)
        embed.add_field(
            name="🎮 Botín total del evento",
            value=(
                f"`{format_number(total_periodo_valor)}` SHULKERS\n"
                f"`{format_number(niveles)}` NIVELES\n"
                f"`{pv}` PVS + `{resto}` SHULKERS"
            ),
            inline=False
        )
        embed.add_field(name="🏆 Estado", value="`TEMPORADA CERRADA`", inline=True)
        embed.add_field(name="💥 Impacto", value=f"`{pv}` PVS", inline=True)
    else:
        embed.add_field(
            name="💎 Botín total del evento",
            value=f"`{format_number(total_periodo_valor)}` {unidad.upper()}",
            inline=False
        )
        embed.add_field(name="🏆 Estado", value="`TEMPORADA CERRADA`", inline=True)
        embed.add_field(name="🚀 Aporte", value=f"`{format_number(total_periodo_valor)}` {unidad.upper()}", inline=True)

    embed.set_footer(text=f"{footer} | Hora Chile")
    return embed


def _lineas_top_compacto(datos, unidad: str = "shulkers", limite: int = 5) -> str:
    if not datos:
        return "_Sin registros_"
    maximo = int(datos[0]["s"] or 0)
    iconos = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = []
    for i, row in enumerate(datos[:limite], start=1):
        user = cortar_nombre(row["username"], 18)
        total = int(row["s"] or 0)
        pct = int(round((total / maximo) * 100)) if maximo > 0 else 0
        icono = iconos[i - 1] if i <= len(iconos) else f"`#{i}`"
        bar = barra_progreso(total, maximo, largo=8)
        lines.append(
            f"{icono} **{user}** — `{format_number(total)}` {unidad} ({pct}%) {bar}"
        )
    return "\n".join(lines)


async def crear_embed_ranking_completo(
    titulo: str,
    color: discord.Color,
    mensual,
    semanal,
    mes_pasado,
    total_mensual: int,
    total_semanal: int,
    total_mes_pasado: int,
    inicio_semana: date,
    inicio_mes_pasado: date,
    fin_mes_pasado: date,
    *,
    mostrar_equivalencias: bool = True,
    unidad: str = "shulkers",
):
    """Un solo embed: mes actual + semana + mes pasado."""
    embed = discord.Embed(
        title=titulo,
        description="Top unificado · menos spam, misma info",
        color=color,
        timestamp=utc_now()
    )

    embed.add_field(
        name="👑 Temporada actual (mes)",
        value=_lineas_top_compacto(mensual, unidad),
        inline=False
    )
    embed.add_field(
        name=f"⚔️ Guerra semanal (desde {inicio_semana})",
        value=_lineas_top_compacto(semanal, unidad),
        inline=False
    )

    if mes_pasado:
        mvp = cortar_nombre(mes_pasado[0]["username"], 18)
        mvp_t = int(mes_pasado[0]["s"] or 0)
        pasado_txt = (
            f"🏆 MVP: **{mvp}** (`{format_number(mvp_t)}` {unidad})\n"
            + _lineas_top_compacto(mes_pasado, unidad)
        )
    else:
        pasado_txt = "_Nadie dejó marca el mes pasado_"
    embed.add_field(
        name=f"👾 Leyendas del mes pasado ({inicio_mes_pasado} → {fin_mes_pasado})",
        value=pasado_txt[:1024],
        inline=False
    )

    if mostrar_equivalencias:
        _, niv_m, pv_m, rest_m = equivalencias(total_mensual)
        _, niv_s, pv_s, rest_s = equivalencias(total_semanal)
        _, niv_p, pv_p, rest_p = equivalencias(total_mes_pasado)
        embed.add_field(
            name="📊 Totales",
            value=(
                f"**Mes:** `{format_number(total_mensual)}` sh · "
                f"`{format_number(niv_m)}` niv · `{pv_m}` PVs + `{rest_m}`\n"
                f"**Semana:** `{format_number(total_semanal)}` sh · "
                f"`{pv_s}` PVs + `{rest_s}`\n"
                f"**Mes pasado:** `{format_number(total_mes_pasado)}` sh · "
                f"`{pv_p}` PVs + `{rest_p}`"
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="📊 Totales",
            value=(
                f"**Mes:** `{format_number(total_mensual)}` {unidad}\n"
                f"**Semana:** `{format_number(total_semanal)}` {unidad}\n"
                f"**Mes pasado:** `{format_number(total_mes_pasado)}` {unidad}"
            ),
            inline=False
        )

    embed.set_footer(text="Hora Chile · 1 embed por ranking")
    return embed


async def total_periodo(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) AS total FROM shulker WHERE {where_sql}", params)
    row = cursor.fetchone()
    return int(row["total"] or 0)

async def total_periodo_rango(tabla: str, desde: str, hasta: str) -> int:
    cursor.execute(
        f"SELECT COALESCE(SUM(total), 0) AS total FROM {tabla} WHERE fecha >= ? AND fecha < ?",
        (desde, hasta)
    )
    row = cursor.fetchone()
    return int(row["total"] or 0)

def obtener_rango_mes_pasado() -> tuple[date, date, date]:
    hoy = today_local()
    inicio_mes_actual = hoy.replace(day=1)
    fin_mes_pasado = inicio_mes_actual - timedelta(days=1)
    inicio_mes_pasado = fin_mes_pasado.replace(day=1)

    if inicio_mes_actual < BOT_START_DATE:
        inicio_mes_actual = BOT_START_DATE
    if inicio_mes_pasado < BOT_START_DATE:
        inicio_mes_pasado = BOT_START_DATE
    if fin_mes_pasado < BOT_START_DATE:
        fin_mes_pasado = BOT_START_DATE

    return inicio_mes_pasado, inicio_mes_actual, fin_mes_pasado

async def actualizar_todos_los_ranking():
    try:
        channel = bot.get_channel(RANKING_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró RANKING_CHANNEL_ID")
            return

        hoy = today_local()
        inicio_mes = clamp_start(hoy.replace(day=1))
        inicio_semana_natural = clamp_start(hoy - timedelta(days=hoy.weekday()))
        inicio_semana = max(inicio_semana_natural, inicio_mes)
        inicio_mes_pasado, inicio_mes_actual, fin_mes_pasado = obtener_rango_mes_pasado()

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
            WHERE fecha >= ? AND fecha < ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_mes_pasado), str(inicio_mes_actual)))
        mensual_pasado = cursor.fetchall()
        total_mensual_pasado = await total_periodo_rango(
            "shulker",
            str(inicio_mes_pasado),
            str(inicio_mes_actual)
        )

        embed = await crear_embed_ranking_completo(
            "🏝️ ISLA PRINCIPAL — RANKING",
            discord.Color.purple(),
            mensual,
            semanal,
            mensual_pasado,
            total_mensual,
            total_semanal,
            total_mensual_pasado,
            inicio_semana,
            inicio_mes_pasado,
            fin_mes_pasado,
            mostrar_equivalencias=True,
            unidad="shulkers",
        )

        for tipo_old in (
            "ranking_diario",
            "ranking_mes_pasado",
            "ranking_mensual",
            "ranking_semanal",
        ):
            await eliminar_mensaje_fijo_si_existe(channel, tipo_old)

        msg = await obtener_mensaje_fijo(channel, "ranking_principal_unificado")
        await msg.edit(content=None, embed=embed)
        await limpiar_mensajes_duplicados_por_titulo(
            channel,
            [embed.title],
            keep_message_ids=[msg.id],
        )
        print("✅ Ranking Principal unificado actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_todos_los_ranking: {e}")

# ===============================
# RANKINGS ISLA SECUNDARIA
# ===============================
async def actualizar_rankings_secundaria():
    try:
        channel = bot.get_channel(SECUNDARIA_RANKING_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró SECUNDARIA_RANKING_CHANNEL_ID")
            return

        hoy = today_local()
        inicio_mes = clamp_start(hoy.replace(day=1))
        inicio_semana_natural = clamp_start(hoy - timedelta(days=hoy.weekday()))
        inicio_semana = max(inicio_semana_natural, inicio_mes)
        inicio_mes_pasado, inicio_mes_actual, fin_mes_pasado = obtener_rango_mes_pasado()

        cursor.execute("""
            SELECT username, SUM(total) as s
            FROM shulker_secundaria
            WHERE fecha >= ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_mes),))
        mensual = cursor.fetchall()
        total_mensual = total_secundaria_periodo("fecha >= ?", (str(inicio_mes),))

        cursor.execute("""
            SELECT username, SUM(total) as s
            FROM shulker_secundaria
            WHERE fecha >= ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_semana),))
        semanal = cursor.fetchall()
        total_semanal = total_secundaria_periodo("fecha >= ?", (str(inicio_semana),))

        cursor.execute("""
            SELECT username, SUM(total) as s
            FROM shulker_secundaria
            WHERE fecha >= ? AND fecha < ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_mes_pasado), str(inicio_mes_actual)))
        mensual_pasado = cursor.fetchall()
        total_mensual_pasado = await total_periodo_rango(
            "shulker_secundaria",
            str(inicio_mes_pasado),
            str(inicio_mes_actual)
        )

        embed = await crear_embed_ranking_completo(
            "🌿 ISLA SECUNDARIA — RANKING",
            discord.Color.teal(),
            mensual,
            semanal,
            mensual_pasado,
            total_mensual,
            total_semanal,
            total_mensual_pasado,
            inicio_semana,
            inicio_mes_pasado,
            fin_mes_pasado,
            mostrar_equivalencias=True,
            unidad="shulkers",
        )

        for tipo_old in (
            "ranking_secundaria_mes_pasado",
            "ranking_secundaria_mensual",
            "ranking_secundaria_semanal",
        ):
            await eliminar_mensaje_fijo_si_existe(channel, tipo_old)

        msg = await obtener_mensaje_fijo(channel, "ranking_secundaria_unificado")
        await msg.edit(content=None, embed=embed)
        await limpiar_mensajes_duplicados_por_titulo(
            channel,
            [embed.title],
            keep_message_ids=[msg.id],
        )
        print("✅ Ranking Secundaria unificado actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_rankings_secundaria: {e}")

# ===============================
# ESTADO ACTUAL DEL EVENTO
# ===============================
def _texto_top_evento(top) -> str:
    if not top:
        return "_Nadie registró en este evento_"
    maximo = int(top[0]["s"] or 0)
    lines = []
    for i, row in enumerate(top, start=1):
        user = cortar_nombre(row["username"], 20)
        t = int(row["s"] or 0)
        medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`#{i}`"
        porcentaje = int(round((t / maximo) * 100)) if maximo > 0 else 0
        bar_user = barra_progreso(t, maximo, largo=10)
        lines.append(f"{medalla} **{user}** — `{t}` shulkers ({porcentaje}%)\n{bar_user}")
    return "\n\n".join(lines)


def construir_embed_evento_activo():
    if EVENTO_TIPO == "end":
        desc = (
            f"**Estado:** 🟢 EN CURSO\n"
            f"**Tipo:** {_label_tipo_evento()}\n"
            f"Puedes registrar shulkers eligiendo **Evento** en el menú de registro."
        )
    else:
        desc = (
            f"**Estado:** 🟢 EN CURSO\n"
            f"**Tipo:** {_label_tipo_evento()}\n"
            f"Este evento **no** usa registro de shulkers en el bot.\n"
            f"El progreso se mide en el juego / según las reglas del staff."
        )

    embed = discord.Embed(
        title=f"🎉 {EVENTO_NOMBRE}",
        description=desc,
        color=discord.Color.gold(),
        timestamp=utc_now()
    )

    if EVENTO_OBJETIVO:
        embed.add_field(name="🎯 Objetivo", value=EVENTO_OBJETIVO[:1024], inline=False)

    if EVENTO_TIPO == "end":
        total = total_evento_shulkers()
        faltan = max(0, EVENTO_GOAL_SHULKERS - total)
        pv_actual, sh_actual = shulkers_a_pv_y_shulkers(total)
        pv_faltan, sh_faltan = shulkers_a_pv_y_shulkers(faltan)
        pct = (total / EVENTO_GOAL_SHULKERS) * 100 if EVENTO_GOAL_SHULKERS else 0
        bar = barra_meta(total, EVENTO_GOAL_SHULKERS, largo=20)
        top = get_evento_top(10)
        embed.add_field(
            name="📦 Meta (End / PVs)",
            value=(
                f"**{EVENTO_GOAL_PVS} PVs** (`{EVENTO_GOAL_SHULKERS}` shulkers)\n"
                f"`{bar}` `{pct:.1f}%`\n"
                f"**Registrado:** `{total}` shulkers (`{pv_actual}` PVs + `{sh_actual}`)\n"
                f"**Faltan:** `{faltan}` shulkers (`{pv_faltan}` PVs + `{sh_faltan}`)"
            ),
            inline=False
        )
        embed.add_field(name="🏆 Top del Evento", value=_texto_top_evento(top), inline=False)

    if EVENTO_PARTICIPANTES:
        embed.add_field(name="👥 Participantes", value=EVENTO_PARTICIPANTES[:1024], inline=False)
    if EVENTO_RECOMPENSAS:
        embed.add_field(name="🎁 Recompensas", value=EVENTO_RECOMPENSAS[:1024], inline=False)
    if EVENTO_REGLAS:
        embed.add_field(name="📜 Reglas", value=EVENTO_REGLAS[:1024], inline=False)

    embed.set_footer(text=f"Desde {EVENTO_START_DATE} | Hora Chile")
    return embed


def construir_embed_evento_terminado():
    if EVENTO_TIPO == "end":
        total = total_evento_shulkers()
        pv_actual, sh_actual = shulkers_a_pv_y_shulkers(total)
        top = get_evento_top(10)
        ganador = top[0] if top else None
        if ganador:
            g_name = ganador["username"]
            g_total = int(ganador["s"] or 0)
            g_pv, g_sh = shulkers_a_pv_y_shulkers(g_total)
            desc = (
                f"## 🏆 ¡Felicitaciones a **{g_name}**!\n"
                f"Ganador con **`{g_total}`** shulkers (`{g_pv}` PVs + `{g_sh}`)\n\n"
                f"El evento **{EVENTO_NOMBRE}** ha terminado.\n"
                f"Ya **no se pueden registrar** más shulkers de evento."
            )
        else:
            desc = (
                f"El evento **{EVENTO_NOMBRE}** ha terminado.\n"
                f"No hubo registros válidos de End en el bot."
            )
    else:
        desc = (
            f"El evento **{EVENTO_NOMBRE}** ha terminado.\n"
            f"**Tipo:** {_label_tipo_evento()}\n"
            f"Los ganadores se definen según el ranking/reglas del juego o del staff."
        )
        top = None
        total = 0
        pv_actual = sh_actual = 0

    embed = discord.Embed(
        title=f"🏁 {EVENTO_NOMBRE} — TERMINADO",
        description=desc,
        color=discord.Color.dark_gold(),
        timestamp=utc_now()
    )

    if EVENTO_OBJETIVO:
        embed.add_field(name="🎯 Objetivo", value=EVENTO_OBJETIVO[:1024], inline=False)

    if EVENTO_TIPO == "end":
        embed.add_field(
            name="📦 Resumen final",
            value=(
                f"**Meta:** `{EVENTO_GOAL_PVS}` PVs (`{EVENTO_GOAL_SHULKERS}` shulkers)\n"
                f"**Total team:** `{total}` shulkers (`{pv_actual}` PVs + `{sh_actual}`)\n"
                f"**Periodo:** `{EVENTO_START_DATE}` → fin"
            ),
            inline=False
        )
        embed.add_field(name="🏅 Clasificación final", value=_texto_top_evento(top), inline=False)

    if EVENTO_RECOMPENSAS:
        embed.add_field(name="🎁 Recompensas", value=EVENTO_RECOMPENSAS[:1024], inline=False)
    embed.set_footer(text="Panel permanente del evento cerrado | Hora Chile")
    return embed


async def actualizar_estado_evento():
    try:
        channel = bot.get_channel(EVENTO_RANKING_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró EVENTO_RANKING_CHANNEL_ID")
            return

        if EVENTO_STATUS == "ended":
            embed = construir_embed_evento_terminado()
        elif EVENTO_STATUS == "active":
            embed = construir_embed_evento_activo()
        else:
            embed = discord.Embed(
                title="🎉 Eventos",
                description=(
                    "No hay un evento activo en este momento.\n"
                    "Cuando el staff cree uno nuevo, aparecerá aquí."
                ),
                color=discord.Color.dark_grey(),
                timestamp=utc_now()
            )

        msg = await obtener_mensaje_fijo(channel, "estado_evento")
        await msg.edit(content=None, embed=embed)
        print("✅ Estado del Evento actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_estado_evento: {e}")

# ===============================
# RANKINGS END APORTADA
# ===============================
async def total_periodo_end(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) AS total FROM end_aportado WHERE {where_sql}", params)
    row = cursor.fetchone()
    return int(row["total"] or 0)

async def actualizar_rankings_end():
    try:
        channel = bot.get_channel(END_APORTE_RANKING_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró END_APORTE_RANKING_CHANNEL_ID")
            return

        hoy = today_local()
        inicio_mes = clamp_start(hoy.replace(day=1))
        inicio_semana_natural = clamp_start(hoy - timedelta(days=hoy.weekday()))
        inicio_semana = max(inicio_semana_natural, inicio_mes)
        inicio_mes_pasado, inicio_mes_actual, fin_mes_pasado = obtener_rango_mes_pasado()

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
            WHERE fecha >= ? AND fecha < ?
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 5
        """, (str(inicio_mes_pasado), str(inicio_mes_actual)))
        mensual_pasado = cursor.fetchall()
        total_mensual_pasado = await total_periodo_rango(
            "end_aportado",
            str(inicio_mes_pasado),
            str(inicio_mes_actual)
        )

        embed = await crear_embed_ranking_completo(
            "🪨 END APORTADA — RANKING",
            discord.Color.dark_gray(),
            mensual,
            semanal,
            mensual_pasado,
            total_mensual,
            total_semanal,
            total_mensual_pasado,
            inicio_semana,
            inicio_mes_pasado,
            fin_mes_pasado,
            mostrar_equivalencias=False,
            unidad="end",
        )

        for tipo_old in (
            "ranking_end_aportada_diario",
            "ranking_end_aportada_mes_pasado",
            "ranking_end_aportada_mensual",
            "ranking_end_aportada_semanal",
        ):
            await eliminar_mensaje_fijo_si_existe(channel, tipo_old)

        msg = await obtener_mensaje_fijo(channel, "ranking_end_unificado")
        await msg.edit(content=None, embed=embed)
        await limpiar_mensajes_duplicados_por_titulo(
            channel,
            [embed.title],
            keep_message_ids=[msg.id],
        )
        print("✅ Ranking END APORTADA unificado actualizado")
    except Exception as e:
        print(f"❌ Error en actualizar_rankings_end: {e}")

# ===============================
# ESTADÍSTICAS PERSONALES
# ===============================
def start_of_week_local(ref: date | None = None) -> date:
    ref = ref or today_local()
    return clamp_start(ref - timedelta(days=ref.weekday()))


def start_of_month_local(ref: date | None = None) -> date:
    ref = ref or today_local()
    return clamp_start(ref.replace(day=1))


def date_range_sum(tabla: str, user_id: int, desde: str, hasta: str) -> int:
    cursor.execute(
        f"SELECT COALESCE(SUM(total), 0) AS total FROM {tabla} WHERE user_id = ? AND fecha >= ? AND fecha < ?",
        (user_id, desde, hasta)
    )
    row = cursor.fetchone()
    return int(row["total"] or 0)


def all_time_sum(tabla: str, user_id: int) -> int:
    cursor.execute(
        f"SELECT COALESCE(SUM(total), 0) AS total FROM {tabla} WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    return int(row["total"] or 0)


def best_day_sum(tabla: str, user_id: int) -> tuple[str, int]:
    cursor.execute(
        f"""
        SELECT fecha, total
        FROM {tabla}
        WHERE user_id = ?
        ORDER BY total DESC, fecha DESC
        LIMIT 1
        """,
        (user_id,)
    )
    row = cursor.fetchone()
    if not row:
        return "Sin registros", 0
    return str(row["fecha"]), int(row["total"] or 0)


def pending_end_requests_count(user_id: int) -> int:
    cursor.execute(
        "SELECT COUNT(*) AS c FROM end_requests WHERE user_id = ? AND status = 'pending'",
        (user_id,)
    )
    row = cursor.fetchone()
    return int(row["c"] or 0)


def latest_approved_end_date(user_id: int) -> str:
    cursor.execute(
        "SELECT MAX(fecha) AS ultima FROM end_aportado WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    return str(row["ultima"] or "Sin aprobaciones")


def get_position_in_period(tabla: str, user_id: int, desde: str, hasta: str | None = None) -> tuple[int | None, int]:
    if hasta is None:
        cursor.execute(
            f"""
            SELECT user_id, SUM(total) AS s
            FROM {tabla}
            WHERE fecha >= ?
            GROUP BY user_id
            ORDER BY s DESC
            """,
            (desde,)
        )
    else:
        cursor.execute(
            f"""
            SELECT user_id, SUM(total) AS s
            FROM {tabla}
            WHERE fecha >= ? AND fecha < ?
            GROUP BY user_id
            ORDER BY s DESC
            """,
            (desde, hasta)
        )
    rows = cursor.fetchall()
    for idx, row in enumerate(rows, start=1):
        if int(row["user_id"]) == user_id:
            return idx, int(row["s"] or 0)
    return None, 0


def points_to_top5(tabla: str, user_id: int, desde: str) -> tuple[int | None, int | None]:
    cursor.execute(
        f"""
        SELECT user_id, SUM(total) AS s
        FROM {tabla}
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        """,
        (desde,)
    )
    rows = cursor.fetchall()
    if not rows:
        return None, 1

    top5_cut = int(rows[4]["s"] or 0) if len(rows) >= 5 else int(rows[-1]["s"] or 0)
    my_total = 0
    my_pos = None
    for idx, row in enumerate(rows, start=1):
        if int(row["user_id"]) == user_id:
            my_total = int(row["s"] or 0)
            my_pos = idx
            break

    if my_pos is not None and my_pos <= 5:
        return 0, my_pos

    needed = max(0, top5_cut - my_total + 1)
    return needed, my_pos


def build_personal_stats_embed(member: discord.abc.User) -> discord.Embed:
    user_id = member.id
    hoy = today_local()

    inicio_semana = str(start_of_week_local(hoy))
    inicio_mes = str(start_of_month_local(hoy))
    inicio_semana_pasada_date = start_of_week_local(hoy) - timedelta(days=7)
    fin_semana_pasada_date = start_of_week_local(hoy)
    inicio_mes_pasado_date = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)
    fin_mes_pasado_date = hoy.replace(day=1)
    manana = str(hoy + timedelta(days=1))

    sh_semana = date_range_sum("shulker", user_id, inicio_semana, manana)
    sh_mes = date_range_sum("shulker", user_id, inicio_mes, manana)
    sh_total = all_time_sum("shulker", user_id)
    sh_best_day_date, sh_best_day_total = best_day_sum("shulker", user_id)

    end_semana = date_range_sum("end_aportado", user_id, inicio_semana, manana)
    end_mes = date_range_sum("end_aportado", user_id, inicio_mes, manana)
    end_total = all_time_sum("end_aportado", user_id)
    end_best_day_date, end_best_day_total = best_day_sum("end_aportado", user_id)
    end_pending = pending_end_requests_count(user_id)
    end_latest = latest_approved_end_date(user_id)

    sh_prev_week = date_range_sum("shulker", user_id, str(inicio_semana_pasada_date), str(fin_semana_pasada_date))
    sh_prev_month = date_range_sum("shulker", user_id, str(inicio_mes_pasado_date), str(fin_mes_pasado_date))
    end_prev_week = date_range_sum("end_aportado", user_id, str(inicio_semana_pasada_date), str(fin_semana_pasada_date))
    end_prev_month = date_range_sum("end_aportado", user_id, str(inicio_mes_pasado_date), str(fin_mes_pasado_date))

    sh_week_pos, _ = get_position_in_period("shulker", user_id, inicio_semana)
    sh_month_pos, _ = get_position_in_period("shulker", user_id, inicio_mes)
    end_week_pos, _ = get_position_in_period("end_aportado", user_id, inicio_semana)
    end_month_pos, _ = get_position_in_period("end_aportado", user_id, inicio_mes)

    sh_to_top5_week, _ = points_to_top5("shulker", user_id, inicio_semana)
    end_to_top5_week, _ = points_to_top5("end_aportado", user_id, inicio_semana)

    def pos_text(pos: int | None) -> str:
        return f"#{pos}" if pos else "Fuera del ranking"

    def trend(current: int, previous: int) -> str:
        diff = current - previous
        if diff > 0:
            return f"+{diff} vs periodo anterior"
        if diff < 0:
            return f"{diff} vs periodo anterior"
        return "Sin cambio vs periodo anterior"

    def top5_text(value: int | None, unidad: str) -> str:
        if value == 0:
            return "Ya estás dentro del Top 5"
        if value is None:
            return "Aún no hay referencia suficiente"
        return f"Te faltan {value} {unidad} para Top 5 semanal"

    embed = discord.Embed(
        title="🎯 Tus estadísticas personales",
        description=(
            "Este resumen es privado y se actualiza con los datos reales del bot.\n"
            "Úsalo para medir tu progreso sin llenar el canal."
        ),
        color=discord.Color.dark_teal(),
        timestamp=utc_now()
    )

    embed.add_field(
        name="📦 Mis Shulkers",
        value=(
            f"**Semanal:** `{sh_semana:,}`\n"
            f"**Mensual:** `{sh_mes:,}`\n"
            f"**Histórico:** `{sh_total:,}`\n"
            f"**Mejor día:** `{sh_best_day_total:,}` ({sh_best_day_date})"
        ),
        inline=False
    )

    embed.add_field(
        name="🪨 Mi End Aportada",
        value=(
            f"**Semanal:** `{end_semana:,}`\n"
            f"**Mensual:** `{end_mes:,}`\n"
            f"**Aprobada histórica:** `{end_total:,}`\n"
            f"**Pendiente de revisión:** `{end_pending}`\n"
            f"**Última aprobada:** `{end_latest}`"
        ),
        inline=False
    )

    embed.add_field(
        name="🎯 Mi Progreso",
        value=(
            f"**Posición semanal Shulker:** `{pos_text(sh_week_pos)}`\n"
            f"**Posición mensual Shulker:** `{pos_text(sh_month_pos)}`\n"
            f"**Posición semanal End:** `{pos_text(end_week_pos)}`\n"
            f"**Posición mensual End:** `{pos_text(end_month_pos)}`\n"
            f"**Shulker:** {top5_text(sh_to_top5_week, 'shulkers')}\n"
            f"**End:** {top5_text(end_to_top5_week, 'end aportada')}"
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 Mi Rendimiento",
        value=(
            f"**Shulker semanal:** `{trend(sh_semana, sh_prev_week)}`\n"
            f"**Shulker mensual:** `{trend(sh_mes, sh_prev_month)}`\n"
            f"**End semanal:** `{trend(end_semana, end_prev_week)}`\n"
            f"**End mensual:** `{trend(end_mes, end_prev_month)}`\n"
            f"**Mejor día End:** `{end_best_day_total:,}` ({end_best_day_date})"
        ),
        inline=False
    )

    embed.set_footer(text=f"Usuario: {member.display_name} | Hora Chile")
    return embed

# ===============================
# PUBLICACIÓN / REPARACIÓN
# ===============================
async def publicar_boton_shulker():
    form_channel = bot.get_channel(FORM_CHANNEL_ID)
    if form_channel:
        msg = await obtener_mensaje_fijo(form_channel, "form_boton")
        await msg.edit(
            content=None,
            embed=discord.Embed(
                title="📦 Registro de Shulker",
                description=(
                    "Presiona el botón para registrar.\n\n"
                    "Al pulsar podrás elegir:\n"
                    "🏝️ **Isla Principal**\n"
                    "🌿 **Isla Secundaria**\n"
                    "🎉 **Evento**"
                ),
                color=discord.Color.green(),
                timestamp=utc_now()
            ),
            view=ShulkerButton()
        )
        print("✅ Botón fijo shulker actualizado")

async def publicar_boton_end_aportada():
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
                    "5. Solo lo aprobado entra al top público\n\n"
                    "⚠️ En este canal no se permite escribir texto.\n"
                    "Solo se acepta la imagen de evidencia después de registrar la cantidad."
                ),
                color=discord.Color.blurple(),
                timestamp=utc_now()
            ),
            view=EndAportadoButton()
        )
        print("✅ Botón fijo END aportada actualizado")

async def publicar_panel_estadisticas():
    try:
        if not STATS_PANEL_CHANNEL_ID:
            return

        channel = bot.get_channel(STATS_PANEL_CHANNEL_ID)
        if not channel:
            print("⚠️ No se encontró STATS_PANEL_CHANNEL_ID")
            return

        msg = await obtener_mensaje_fijo(channel, "panel_estadisticas_personales")
        embed = discord.Embed(
            title="📊 Panel de estadísticas personales",
            description=(
                "Presiona el botón para ver tu resumen privado.\n\n"
                "🔒 La respuesta será **ephemeral** y solo tú podrás verla.\n"
                "📦 Incluye shulkers, End aportada, progreso y rendimiento."
            ),
            color=discord.Color.dark_teal(),
            timestamp=utc_now()
        )
        await msg.edit(content=None, embed=embed, view=StatsPanelView())
        print("✅ Panel de estadísticas personales actualizado")
    except Exception as e:
        print(f"❌ Error en publicar_panel_estadisticas: {e}")

async def sincronizar_mensajes_revision():
    try:
        review_channel = bot.get_channel(END_APORTE_REVIEW_CHANNEL_ID)
        if not review_channel:
            print("⚠️ No se encontró canal de revisión para sincronizar")
            return

        cursor.execute("""
            SELECT id, review_message_id
            FROM end_requests
            WHERE review_message_id IS NOT NULL
        """)
        rows = cursor.fetchall()

        actualizados = 0

        for row in rows:
            request_id = row["id"]
            review_message_id = row["review_message_id"]

            request_row = get_request_by_id(request_id)
            if not request_row:
                continue

            disabled = request_row["status"] != "pending"

            try:
                msg = await review_channel.fetch_message(review_message_id)
                embed = construir_embed_revision(request_row)
                await msg.edit(embed=embed, view=EndReviewView(disabled=disabled))
                actualizados += 1
            except Exception as e:
                print(f"⚠️ No se pudo sincronizar revisión #{request_id}: {e}")

        print(f"✅ Mensajes de revisión sincronizados: {actualizados}")
    except Exception as e:
        print(f"❌ Error en sincronizar_mensajes_revision: {e}")

# ===============================
# TASK
# ===============================
# ===============================
# DEBOUNCE DE RANKINGS (más rápido al registrar)
# ===============================
_ranking_debounce_tasks: dict[str, asyncio.Task] = {}
RANKING_DEBOUNCE_SECONDS = 2.5  # espera breve y agrupa actualizaciones

async def _ejecutar_update_ranking(destino: str):
    try:
        if destino == "principal":
            await actualizar_todos_los_ranking()
            await actualizar_panel_progreso()
        elif destino == "secundaria":
            await actualizar_rankings_secundaria()
            await actualizar_panel_progreso()
        elif destino == "evento":
            await actualizar_estado_evento()
        elif destino == "end":
            await actualizar_rankings_end()
    except Exception as e:
        print(f"❌ Error actualizando ranking ({destino}): {e}")

async def schedule_ranking_update(destino: str):
    """Agrupa actualizaciones: si hay varios registros seguidos, solo actualiza una vez."""
    prev = _ranking_debounce_tasks.get(destino)
    if prev and not prev.done():
        prev.cancel()

    async def _debounced():
        try:
            await asyncio.sleep(RANKING_DEBOUNCE_SECONDS)
            await _ejecutar_update_ranking(destino)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Error en debounce ranking ({destino}): {e}")

    _ranking_debounce_tasks[destino] = asyncio.create_task(_debounced())

@tasks.loop(hours=1)
async def ranking_automatico():
    cleanup_expired_cooldowns()
    cleanup_expired_pending_end()
    await actualizar_todos_los_ranking()
    await actualizar_panel_progreso()
    await actualizar_rankings_end()
    await actualizar_rankings_secundaria()
    await actualizar_estado_evento()
    await actualizar_panel_stock()
    await actualizar_panel_cumpleanos()
    await anunciar_cumpleanos_del_dia()
    await actualizar_panel_funciones()
    await evaluar_minimos_semana_anterior()

async def actualizar_shulker_post_registro(
    interaction: discord.Interaction,
    cantidad_int: int,
    nuevo_total: int,
    destino: str = "principal"
):
    try:
        nombres = {
            "principal": "Isla Principal",
            "secundaria": "Isla Secundaria",
            "evento": "Evento"
        }
        nombre_destino = nombres.get(destino, destino)

        colores = {
            "principal": discord.Color.green(),
            "secundaria": discord.Color.teal(),
            "evento": discord.Color.gold()
        }
        color = colores.get(destino, discord.Color.green())

        emojis = {
            "principal": "🏝️",
            "secundaria": "🌿",
            "evento": "🎉"
        }
        emoji = emojis.get(destino, "📦")

        # 1) Log inmediato (lo que el usuario ve más rápido)
        end_channel = interaction.client.get_channel(END_CHANNEL_ID)
        if end_channel:
            embed = discord.Embed(
                title=f"{emoji} Registro de Shulker",
                description=(
                    f"👤 {interaction.user.mention}\n"
                    f"📍 Destino: **{nombre_destino}**\n"
                    f"➕ Registró: `{cantidad_int}`\n"
                    f"📊 Total hoy: `{nuevo_total}`"
                ),
                color=color,
                timestamp=utc_now()
            )
            await end_channel.send(embed=embed)

        # 2) Ranking con debounce (agrupa varios registros seguidos)
        await schedule_ranking_update(destino)

    except Exception as e:
        print(f"❌ Error en actualizar_shulker_post_registro: {e}")

# ===============================
# MENÚ DE DESTINO + MODAL SHULKER
# ===============================
class DestinoSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Isla Principal",
                value="principal",
                emoji="🏝️",
                description="Se suma al ranking principal"
            ),
            discord.SelectOption(
                label="Isla Secundaria",
                value="secundaria",
                emoji="🌿",
                description="Se suma al ranking de la isla secundaria"
            ),
        ]
        if evento_usa_registro_shulker():
            options.append(
                discord.SelectOption(
                    label=f"Evento: {EVENTO_NOMBRE[:60]}",
                    value="evento",
                    emoji="🎉",
                    description=f"Meta {EVENTO_GOAL_PVS} PVs — en curso"
                )
            )
        super().__init__(
            placeholder="¿Dónde quieres registrar las shulkers?",
            options=options,
            min_values=1,
            max_values=1,
            custom_id="destino_shulker_select_v1"
        )

    async def callback(self, interaction: discord.Interaction):
        destino = self.values[0]
        if destino == "evento" and not evento_usa_registro_shulker():
            await interaction.response.send_message(
                "🏁 No hay un evento de End activo para registrar shulkers.",
                ephemeral=True
            )
            try:
                await interaction.message.delete()
            except Exception:
                pass
            return

        # Principal / Secundaria: preguntar origen (almacén vs propia)
        if destino in ("principal", "secundaria"):
            await interaction.response.send_message(
                f"📦 **¿De dónde salió la End** que vas a registrar en "
                f"**{'Isla Principal' if destino == 'principal' else 'Isla Secundaria'}**?\n\n"
                f"• **Del almacén del team** → se resta del stock\n"
                f"• **Mía / otra fuente** → solo ranking, **no** resta stock",
                view=OrigenEndView(destino=destino),
                ephemeral=True
            )
            try:
                await interaction.message.delete()
            except Exception:
                pass
            return

        await interaction.response.send_modal(ShulkerModal(destino=destino, origen=None))
        try:
            await interaction.message.delete()
        except Exception:
            pass


class DestinoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(DestinoSelect())


class OrigenEndView(discord.ui.View):
    def __init__(self, destino: str):
        super().__init__(timeout=60)
        self.destino = destino

    @discord.ui.button(
        label="Del almacén del team",
        style=discord.ButtonStyle.primary,
        emoji="📦"
    )
    async def origen_almacen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ShulkerModal(destino=self.destino, origen="almacen")
        )

    @discord.ui.button(
        label="Mía / otra fuente",
        style=discord.ButtonStyle.secondary,
        emoji="🎒"
    )
    async def origen_propia(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ShulkerModal(destino=self.destino, origen="propia")
        )


class ShulkerModal(discord.ui.Modal, title="Registro de Shulker"):
    cantidad = discord.ui.TextInput(label="¿Cuántas shulker colocaste?", required=True)

    def __init__(self, destino: str = "principal", origen: str | None = None):
        super().__init__()
        self.destino = destino
        # "almacen" | "propia" | None (evento)
        self.origen = origen

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = interaction.user.id
            await interaction.response.defer(ephemeral=True)

            restante = get_cooldown_remaining("shulker_normal", user_id)
            if restante > 0:
                await interaction.followup.send(
                    f"⏳ Espera `{restante}` segundos antes de registrar otra vez.",
                    ephemeral=True
                )
                return

            try:
                cantidad_int = int(self.cantidad.value)
                if cantidad_int <= 0:
                    raise ValueError
            except ValueError:
                await interaction.followup.send("❌ Número inválido.", ephemeral=True)
                return

            if self.destino == "evento" and not evento_usa_registro_shulker():
                await interaction.followup.send(
                    "🏁 No hay un evento de End activo para registrar shulkers.",
                    ephemeral=True
                )
                return

            hoy = local_date_str()
            username = interaction.user.display_name

            if self.destino == "principal":
                tabla = "shulker"
            elif self.destino == "secundaria":
                tabla = "shulker_secundaria"
            else:
                tabla = "shulker_evento"

            cursor.execute(f"""
                INSERT INTO {tabla} (user_id, username, fecha, total)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, fecha)
                DO UPDATE SET
                    total = total + excluded.total,
                    username = excluded.username
            """, (user_id, username, hoy, cantidad_int))
            db.commit()

            set_cooldown("shulker_normal", user_id, COOLDOWN_SECONDS)

            cursor.execute(
                f"SELECT total FROM {tabla} WHERE user_id = ? AND fecha = ?",
                (user_id, hoy)
            )
            row = cursor.fetchone()
            nuevo_total = int(row["total"] or 0) if row else cantidad_int

            nombres = {
                "principal": "Isla Principal",
                "secundaria": "Isla Secundaria",
                "evento": "Evento"
            }
            nombre_destino = nombres.get(self.destino, self.destino)

            # Contabilidad de almacén (solo principal / secundaria)
            stock_msg = ""
            if self.destino in ("principal", "secundaria") and self.origen == "almacen":
                restado, queda = remove_stock_shulkers(self.destino, cantidad_int)
                if restado == 0:
                    stock_msg = (
                        f"\n📦 Stock **{nombre_destino}**: no había End en almacén "
                        f"(quedó en `0`). El registro **sí** se guardó."
                    )
                elif restado < cantidad_int:
                    stock_msg = (
                        f"\n📦 Del almacén se restaron solo `{restado}` "
                        f"(había menos stock). El resto se trató como propio.\n"
                        f"📉 Stock restante **{nombre_destino}**: `{queda}` shulkers."
                    )
                else:
                    stock_msg = (
                        f"\n📦 Restado del almacén: `{restado}` shulkers.\n"
                        f"📉 Stock restante **{nombre_destino}**: `{queda}` shulkers."
                    )
                asyncio.create_task(actualizar_panel_stock())
            elif self.destino in ("principal", "secundaria") and self.origen == "propia":
                stock_msg = "\n🎒 Origen: **propia / otra fuente** (no se restó del almacén)."

            msg = await interaction.followup.send(
                f"✅ Registro guardado en **{nombre_destino}**.\n"
                f"Añadiste `{cantidad_int}` shulkers. Total de hoy: `{nuevo_total}`."
                f"{stock_msg}",
                ephemeral=True
            )

            async def _borrar_exito():
                await asyncio.sleep(8)
                try:
                    await msg.delete()
                except Exception:
                    pass

            asyncio.create_task(_borrar_exito())

            asyncio.create_task(
                actualizar_shulker_post_registro(
                    interaction, cantidad_int, nuevo_total, destino=self.destino
                )
            )

            print(
                f"✅ Registro {nombre_destino} para {username}: +{cantidad_int} "
                f"(origen={self.origen})"
            )
        except Exception as e:
            print(f"❌ Error en modal on_submit: {e}")
            try:
                await interaction.followup.send(
                    f"❌ Error al guardar: `{type(e).__name__}: {e}`",
                    ephemeral=True
                )
            except Exception:
                pass

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
    ubicacion = discord.ui.TextInput(
        label="¿Dónde las dejaste?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
        placeholder="Ej: Cofre del spawn isla principal, fila 3 / Chunk X,Z / Almacén team..."
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = interaction.user.id

            # Responde de inmediato para evitar el timeout visual del modal
            await interaction.response.defer(ephemeral=True, thinking=False)

            pendiente = get_pending_end(user_id)
            if pendiente:
                restante = get_pending_end_remaining_seconds(user_id)
                await interaction.followup.send(
                    f"⏳ Ya tienes un registro pendiente de evidencia. "
                    f"Sube la imagen en este canal o espera `{max(1, restante)}` segundos para que expire.",
                    ephemeral=True
                )
                return

            restante = get_cooldown_remaining("end_aportada", user_id)
            if restante > 0:
                await interaction.followup.send(
                    f"⏳ Espera `{restante}` segundos antes de registrar otra aportación.",
                    ephemeral=True
                )
                return

            try:
                cantidad_int = int(self.cantidad.value)
                if cantidad_int <= 0:
                    raise ValueError
            except ValueError:
                await interaction.followup.send(
                    "❌ Número inválido. Debes escribir un número entero mayor que 0.",
                    ephemeral=True
                )
                return

            ubicacion_txt = str(self.ubicacion.value or "").strip()
            if len(ubicacion_txt) < 3:
                await interaction.followup.send(
                    "❌ Indica con más detalle **dónde dejaste** la End (mín. 3 caracteres).",
                    ephemeral=True
                )
                return

            save_pending_end(
                user_id=user_id,
                username=interaction.user.display_name,
                fecha=local_date_str(),
                cantidad=cantidad_int,
                channel_id=interaction.channel.id if interaction.channel else 0,
                ubicacion=ubicacion_txt,
            )

            set_cooldown("end_aportada", user_id, COOLDOWN_SECONDS)

            await interaction.followup.send(
                "📸 **Paso 2/2:** ahora sube **una imagen** como evidencia.\n"
                f"📍 Ubicación registrada: `{ubicacion_txt[:120]}`\n"
                f"⏳ Tienes `{END_UPLOAD_TIMEOUT_SECONDS}` segundos.\n"
                "✅ Staff verá la imagen **y** dónde la dejaste.\n"
                "⚠️ Solo lo aprobado contará en el top público.\n"
                "💡 Puedes añadir un comentario junto a la imagen si quieres.",
                ephemeral=True
            )

        except Exception as e:
            print(f"❌ Error en EndAportadoModal.on_submit: {e}")
            try:
                await interaction.followup.send(
                    "❌ Ocurrió un error al iniciar el registro de End aportada.",
                    ephemeral=True
                )
            except Exception:
                pass

# ===============================
# MODAL RECHAZO STAFF
# ===============================
class RejectReasonModal(discord.ui.Modal, title="Rechazar solicitud"):
    reason = discord.ui.TextInput(
        label="Motivo de rechazo",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
        placeholder="Escribe el motivo del rechazo..."
    )

    def __init__(self, review_message_id: int):
        super().__init__()
        self.review_message_id = review_message_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("❌ No se pudo validar tu permiso.", ephemeral=True)
                return

            if not is_staff_member(interaction.user):
                await interaction.response.send_message(
                    "❌ Solo el staff puede rechazar solicitudes.",
                    ephemeral=True
                )
                return

            request_row = get_request_by_review_message_id(self.review_message_id)
            if not request_row:
                await interaction.response.send_message(
                    "❌ No se encontró la solicitud asociada a este mensaje.",
                    ephemeral=True
                )
                return

            if request_row["status"] != "pending":
                await interaction.response.send_message(
                    f"⚠️ Esta solicitud ya fue revisada anteriormente (`{request_row['status']}`).",
                    ephemeral=True
                )
                return

            reviewed_at = local_datetime_str()
            reviewed_by = interaction.user.id
            motivo = str(self.reason.value).strip()

            cursor.execute("""
                UPDATE end_requests
                SET status = 'rejected',
                    rejection_reason = ?,
                    reviewed_by = ?,
                    reviewed_at = ?
                WHERE id = ? AND status = 'pending'
            """, (motivo, reviewed_by, reviewed_at, request_row["id"]))

            if cursor.rowcount == 0:
                db.commit()
                await interaction.response.send_message(
                    "⚠️ Esta solicitud ya no estaba pendiente.",
                    ephemeral=True
                )
                return

            db.commit()

            updated_row = get_request_by_id(request_row["id"])
            embed = construir_embed_revision(
                updated_row,
                reviewer_name=interaction.user.display_name
            )

            await interaction.response.edit_message(
                embed=embed,
                view=EndReviewView(disabled=True)
            )

            try:
                usuario = await bot.fetch_user(request_row["user_id"])
                await usuario.send(
                    f"❌ Tu solicitud de **End aportada** por `{request_row['cantidad']}` fue **rechazada** por el staff.\n"
                    f"📝 Motivo: {motivo}"
                )
            except Exception:
                pass

            await log_staff_action(
                action="end_request_rejected",
                request_id=request_row["id"],
                target_user_id=request_row["user_id"],
                target_username=request_row["username"],
                actor_user_id=interaction.user.id,
                actor_username=interaction.user.display_name,
                amount=request_row["cantidad"],
                reason=motivo
            )

            print(f"❌ Solicitud END #{request_row['id']} rechazada por {interaction.user}")

        except Exception as e:
            print(f"❌ Error en RejectReasonModal.on_submit: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error al rechazar la solicitud.",
                    ephemeral=True
                )

# ===============================
# BOTÓN SHULKER (AHORA CON MENÚ)
# ===============================
class ShulkerButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Registrar Shulker",
        style=discord.ButtonStyle.green,
        emoji="📦",
        custom_id="registrar_shulker_btn_v2"
    )
    async def registrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message(
                "📦 **¿Dónde quieres registrar las shulkers?**",
                view=DestinoView(),
                ephemeral=True
            )
            print(f"✅ Botón pulsado por {interaction.user}")
        except Exception as e:
            print(f"❌ Error al abrir menú: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ No se pudo abrir el menú.",
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
        custom_id="registrar_end_aportada_btn_v2"
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
# PANEL DE ESTADÍSTICAS PERSONALES
# ===============================
class StatsPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎯estadisticas📊",
        style=discord.ButtonStyle.secondary,
        custom_id="panel_estadisticas_personales_v1"
    )
    async def mostrar_estadisticas(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            embed = build_personal_stats_embed(interaction.user)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"❌ Error al mostrar estadísticas personales: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ No se pudieron cargar tus estadísticas en este momento.",
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
        custom_id="end_review_approve_v2"
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_approve(interaction)

    @discord.ui.button(
        label="Rechazar",
        style=discord.ButtonStyle.red,
        emoji="❌",
        custom_id="end_review_reject_v2"
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("❌ No se pudo validar tu permiso.", ephemeral=True)
                return

            if not is_staff_member(interaction.user):
                await interaction.response.send_message(
                    "❌ Solo el staff puede rechazar solicitudes.",
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

            if request_row["status"] != "pending":
                await interaction.response.send_message(
                    f"⚠️ Esta solicitud ya fue revisada anteriormente (`{request_row['status']}`).",
                    ephemeral=True
                )
                return

            await interaction.response.send_modal(RejectReasonModal(interaction.message.id))
        except Exception as e:
            print(f"❌ Error al abrir modal de rechazo: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ No se pudo abrir el formulario de rechazo.",
                    ephemeral=True
                )

    async def handle_approve(self, interaction: discord.Interaction):
        try:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("❌ No se pudo validar tu permiso.", ephemeral=True)
                return

            if not is_staff_member(interaction.user):
                await interaction.response.send_message(
                    "❌ Solo el staff puede aprobar solicitudes.",
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

            request_id = request_row["id"]
            status_actual = request_row["status"]

            if status_actual != "pending":
                await interaction.response.send_message(
                    f"⚠️ Esta solicitud ya fue revisada anteriormente (`{status_actual}`).",
                    ephemeral=True
                )
                return

            reviewed_at = local_datetime_str()
            reviewed_by = interaction.user.id

            cursor.execute("""
                UPDATE end_requests
                SET status = 'approved',
                    rejection_reason = NULL,
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

            cursor.execute("""
                INSERT INTO end_aportado (user_id, username, fecha, total)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, fecha)
                DO UPDATE SET
                    total = total + excluded.total,
                    username = excluded.username
            """, (
                request_row["user_id"],
                request_row["username"],
                request_row["fecha"],
                request_row["cantidad"]
            ))

            db.commit()

            updated_row = get_request_by_id(request_id)
            embed = construir_embed_revision(
                updated_row,
                reviewer_name=interaction.user.display_name
            )

            await interaction.response.edit_message(
                embed=embed,
                view=EndReviewView(disabled=True)
            )

            try:
                usuario = await bot.fetch_user(request_row["user_id"])
                await usuario.send(
                    f"✅ Tu solicitud de **End aportada** por `{request_row['cantidad']}` fue **aprobada**."
                )
            except Exception:
                pass

            await log_staff_action(
                action="end_request_approved",
                request_id=request_id,
                target_user_id=request_row["user_id"],
                target_username=request_row["username"],
                actor_user_id=interaction.user.id,
                actor_username=interaction.user.display_name,
                amount=request_row["cantidad"],
                reason=None
            )

            await actualizar_rankings_end()
            print(f"✅ Solicitud END #{request_id} aprobada por {interaction.user}")

            # Ofrecer sumar al stock del almacén
            try:
                cant = int(request_row["cantidad"] or 0)
                if cant > 0:
                    await interaction.followup.send(
                        f"📦 Solicitud `#{request_id}` aprobada (`{cant}` shulkers).\n"
                        f"¿Sumar esta End al **stock del almacén**?",
                        view=StockAsignarView(cantidad=cant, request_id=request_id),
                        ephemeral=True
                    )
            except Exception as e:
                print(f"⚠️ No se pudo ofrecer asignación de stock: {e}")

        except Exception as e:
            print(f"❌ Error en EndReviewView.handle_approve: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error al aprobar la solicitud.",
                    ephemeral=True
                )


class StockAsignarView(discord.ui.View):
    """Tras aprobar End aportada: sumar al stock de una isla o no sumar."""

    def __init__(self, cantidad: int, request_id: int):
        super().__init__(timeout=300)
        self.cantidad = cantidad
        self.request_id = request_id

    async def _asignar(self, interaction: discord.Interaction, destino: str | None):
        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message(
                "❌ Solo staff puede asignar stock.",
                ephemeral=True
            )
            return

        if destino is None:
            await interaction.response.edit_message(
                content=f"ℹ️ No se sumó al almacén la solicitud `#{self.request_id}`.",
                view=None
            )
            return

        nuevo = add_stock_shulkers(destino, self.cantidad)
        nombre = "Isla Principal" if destino == "principal" else "Isla Secundaria"
        pv, resto = shulkers_a_pv_y_shulkers(nuevo)
        await interaction.response.edit_message(
            content=(
                f"✅ `+{self.cantidad}` shulkers al stock de **{nombre}** "
                f"(req `#{self.request_id}`).\n"
                f"📦 Stock actual: `{nuevo}` shulkers (`{pv}` PVs + `{resto}`)."
            ),
            view=None
        )
        await actualizar_panel_stock()

    @discord.ui.button(label="→ Principal", style=discord.ButtonStyle.success, emoji="🏝️")
    async def a_principal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._asignar(interaction, "principal")

    @discord.ui.button(label="→ Secundaria", style=discord.ButtonStyle.success, emoji="🌿")
    async def a_secundaria(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._asignar(interaction, "secundaria")

    @discord.ui.button(label="No sumar", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def no_sumar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._asignar(interaction, None)

# ===============================
# RESTRICCIÓN DEL CANAL END APORTADA
# ===============================
async def manejar_restriccion_canal_end_aportada(message: discord.Message) -> bool:
    """
    Devuelve True si el mensaje fue consumido por esta lógica.
    """
    try:
        if message.channel.id != END_APORTE_FORM_CHANNEL_ID:
            return False

        if isinstance(message.author, discord.Member) and is_staff_member(message.author):
            if message.content.startswith("!"):
                return False

        pendiente = get_pending_end(message.author.id)

        # Si el usuario tiene registro pendiente, dejamos que pase al flujo de evidencia
        if pendiente:
            consumido = await manejar_subida_pendiente_end(message)
            return consumido

        # Si NO tiene pendiente, en este canal no debe escribir ni subir archivos
        try:
            await message.delete()
        except Exception:
            pass

        if message.attachments:
            await message.channel.send(
                f"{message.author.mention} ❌ Primero debes pulsar el botón **Registrar End Aportada**, "
                f"completar cantidad + ubicación y recién después subir la imagen.",
                delete_after=10
            )
        else:
            await message.channel.send(
                f"{message.author.mention} 🚫 Usa el botón **Registrar End Aportada** "
                f"(cantidad + dónde la dejaste) y luego sube la imagen de evidencia.",
                delete_after=10
            )

        return True
    except Exception as e:
        print(f"❌ Error en manejar_restriccion_canal_end_aportada: {e}")
        return False

# ===============================
# CAPTURA DE IMAGEN PARA END APORTADA
# ===============================
async def manejar_subida_pendiente_end(message: discord.Message) -> bool:
    try:
        pendiente = get_pending_end(message.author.id)
        if not pendiente:
            return False

        if message.content.startswith("!") and not message.attachments:
            return False

        canal_esperado = int(pendiente["channel_id"])

        if canal_esperado and message.channel.id != canal_esperado:
            return False

        if not message.attachments:
            try:
                await message.delete()
            except Exception:
                pass

            await message.channel.send(
                f"{message.author.mention} 📸 Aún estoy esperando tu **imagen de evidencia** para completar el registro de End aportada.",
                delete_after=10
            )
            return True

        imagen = None
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image"):
                imagen = attachment
                break

        if not imagen:
            try:
                await message.delete()
            except Exception:
                pass

            await message.channel.send(
                f"{message.author.mention} ❌ Debes subir **una imagen válida** como evidencia.",
                delete_after=10
            )
            return True

        review_channel = bot.get_channel(END_APORTE_REVIEW_CHANNEL_ID)
        if not review_channel:
            await message.channel.send(
                f"{message.author.mention} ❌ No se encontró el canal privado de revisión del staff.",
                delete_after=10
            )
            return True

        file_bytes = await imagen.read()
        discord_file = discord.File(
            fp=io.BytesIO(file_bytes),
            filename=imagen.filename or "evidencia.png"
        )

        temp_embed = discord.Embed(
            title="🪨 Evidencia recibida",
            description="Archivo reenviado para revisión interna.",
            color=discord.Color.orange(),
            timestamp=utc_now()
        )

        staff_evidence_msg = await review_channel.send(
            content=f"📥 Evidencia enviada por <@{message.author.id}>",
            file=discord_file,
            embed=temp_embed
        )

        if not staff_evidence_msg.attachments:
            await message.channel.send(
                f"{message.author.mention} ❌ No se pudo reenviar la imagen al canal de staff.",
                delete_after=10
            )
            return True

        staff_image_url = staff_evidence_msg.attachments[0].url

        # Ubicación del modal + comentario opcional junto a la imagen
        try:
            ubicacion_base = (pendiente["ubicacion"] or "").strip()
        except (KeyError, IndexError, TypeError):
            ubicacion_base = ""
        comentario_extra = (message.content or "").strip()
        if comentario_extra.startswith("!"):
            comentario_extra = ""
        if ubicacion_base and comentario_extra:
            ubicacion_final = f"{ubicacion_base}\n💬 Nota: {comentario_extra}"[:500]
        elif comentario_extra:
            ubicacion_final = comentario_extra[:500]
        else:
            ubicacion_final = ubicacion_base[:500]

        cursor.execute("""
            INSERT INTO end_requests (
                user_id, username, fecha, cantidad, image_url, status, created_at, ubicacion
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (
            message.author.id,
            pendiente["username"],
            pendiente["fecha"],
            pendiente["cantidad"],
            staff_image_url,
            local_datetime_str(),
            ubicacion_final,
        ))
        db.commit()

        request_id = cursor.lastrowid
        request_row = get_request_by_id(request_id)

        embed = construir_embed_revision(request_row)

        review_msg = await review_channel.send(
            embed=embed,
            view=EndReviewView()
        )

        cursor.execute("""
            UPDATE end_requests
            SET review_message_id = ?
            WHERE id = ?
        """, (review_msg.id, request_id))
        db.commit()

        clear_pending_end(message.author.id)

        await message.channel.send(
            f"{message.author.mention} ✅ Tu evidencia fue enviada al **staff** para revisión.\n"
            f"🆔 Solicitud: `#{request_id}`\n"
            f"⏳ Tu imagen se borrará en {PUBLIC_EVIDENCE_DELETE_SECONDS} segundos.",
            delete_after=10
        )

        try:
            ubi_dm = ubicacion_final[:200] if ubicacion_final else "—"
            await message.author.send(
                f"✅ Tu solicitud de **End aportada** fue registrada correctamente.\n"
                f"🆔 Solicitud: `#{request_id}`\n"
                f"📦 Cantidad: `{pendiente['cantidad']}` end aportada\n"
                f"📍 Ubicación: {ubi_dm}\n"
                f"📅 Fecha: `{pendiente['fecha']}`\n"
                "⏳ Ahora está pendiente de revisión del staff."
            )
        except Exception:
            pass

        try:
            await message.delete(delay=PUBLIC_EVIDENCE_DELETE_SECONDS)
        except Exception:
            pass

        await log_staff_action(
            action="end_request_created",
            request_id=request_id,
            target_user_id=message.author.id,
            target_username=pendiente["username"],
            actor_user_id=message.author.id,
            actor_username=pendiente["username"],
            amount=pendiente["cantidad"],
            reason=None
        )

        print(f"✅ Solicitud END #{request_id} creada por {message.author}")
        return True

    except Exception as e:
        print(f"❌ Error en manejar_subida_pendiente_end: {e}")
        return False

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    consumido_restriccion = await manejar_restriccion_canal_end_aportada(message)
    if consumido_restriccion:
        return

    consumido = await manejar_subida_pendiente_end(message)
    if consumido:
        return

    await bot.process_commands(message)

# ===============================
# COMANDOS
# ===============================
def _tabla_por_destino(destino: str) -> str | None:
    d = (destino or "").strip().lower()
    if d in ("principal", "isla", "main"):
        return "shulker"
    if d in ("secundaria", "segunda", "sec"):
        return "shulker_secundaria"
    if d in ("evento", "event"):
        return "shulker_evento"
    return None


@bot.command(name="vershulker")
@commands.has_permissions(administrator=True)
async def vershulker(ctx, member: discord.Member, destino: str = "principal"):
    """Ver totales de shulkers de un usuario. Uso: !vershulker @user [principal|secundaria|evento]"""
    tabla = _tabla_por_destino(destino)
    if not tabla:
        await ctx.reply(
            "❌ Destino inválido. Usa: `principal`, `secundaria` o `evento`.",
            mention_author=False
        )
        return

    cursor.execute(
        f"""
        SELECT fecha, total
        FROM {tabla}
        WHERE user_id = ?
        ORDER BY fecha DESC
        LIMIT 15
        """,
        (member.id,)
    )
    rows = cursor.fetchall()

    cursor.execute(
        f"SELECT COALESCE(SUM(total), 0) AS s FROM {tabla} WHERE user_id = ?",
        (member.id,)
    )
    total = int(cursor.fetchone()["s"] or 0)

    if not rows:
        await ctx.reply(
            f"ℹ️ {member.mention} no tiene registros en **{destino}**.",
            mention_author=False
        )
        return

    lineas = [f"`{r['fecha']}` → **{int(r['total'])}** shulkers" for r in rows]
    embed = discord.Embed(
        title=f"📦 Shulkers de {member.display_name}",
        description="\n".join(lineas),
        color=discord.Color.blue(),
        timestamp=utc_now()
    )
    embed.add_field(name="Destino", value=f"`{destino}`", inline=True)
    embed.add_field(name="Total histórico", value=f"`{total}`", inline=True)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="quitarshulker")
@commands.has_permissions(administrator=True)
async def quitarshulker(
    ctx,
    member: discord.Member,
    cantidad: int,
    destino: str = "principal",
    fecha: str | None = None
):
    """
    Quita shulkers reportados de más.
    Uso: !quitarshulker @user cantidad [principal|secundaria|evento] [YYYY-MM-DD]
    Si no pones fecha, usa el día de hoy (hora Chile).
    """
    if cantidad <= 0:
        await ctx.reply("❌ La cantidad debe ser mayor que 0.", mention_author=False)
        return

    tabla = _tabla_por_destino(destino)
    if not tabla:
        await ctx.reply(
            "❌ Destino inválido. Usa: `principal`, `secundaria` o `evento`.\n"
            "Ejemplo: `!quitarshulker @user 10 principal`",
            mention_author=False
        )
        return

    fecha_uso = fecha or local_date_str()
    try:
        date.fromisoformat(fecha_uso)
    except ValueError:
        await ctx.reply(
            "❌ Fecha inválida. Usa formato `YYYY-MM-DD` (ejemplo: `2026-07-30`).",
            mention_author=False
        )
        return

    cursor.execute(
        f"SELECT total FROM {tabla} WHERE user_id = ? AND fecha = ?",
        (member.id, fecha_uso)
    )
    row = cursor.fetchone()

    if not row:
        await ctx.reply(
            f"ℹ️ {member.mention} no tiene registro en **{destino}** el día `{fecha_uso}`.",
            mention_author=False
        )
        return

    actual = int(row["total"] or 0)
    if cantidad > actual:
        await ctx.reply(
            f"⚠️ Solo tiene `{actual}` ese día. No se puede quitar `{cantidad}`.\n"
            f"Usa `!vershulker @{member.display_name} {destino}` para ver sus registros.",
            mention_author=False
        )
        return

    nuevo = actual - cantidad

    if nuevo == 0:
        cursor.execute(
            f"DELETE FROM {tabla} WHERE user_id = ? AND fecha = ?",
            (member.id, fecha_uso)
        )
    else:
        cursor.execute(
            f"UPDATE {tabla} SET total = ? WHERE user_id = ? AND fecha = ?",
            (nuevo, member.id, fecha_uso)
        )
    db.commit()

    await ctx.reply(
        f"✅ Corregido **{destino}** de {member.mention}\n"
        f"📅 Fecha: `{fecha_uso}`\n"
        f"➖ Quitado: `{cantidad}`\n"
        f"📊 Antes: `{actual}` → Ahora: `{nuevo}`",
        mention_author=False
    )

    # Refrescar rankings del destino
    if tabla == "shulker":
        await actualizar_todos_los_ranking()
        await actualizar_panel_progreso()
    elif tabla == "shulker_secundaria":
        await actualizar_rankings_secundaria()
        await actualizar_panel_progreso()
    else:
        await actualizar_estado_evento()


@bot.command(name="setnivel")
@commands.has_permissions(administrator=True)
async def setnivel(ctx, nivel: int, destino: str = "principal"):
    """
    Fija el nivel base de una isla y recalibra el progreso.
    Uso: !setnivel 17753228
         !setnivel 500000 secundaria
    """
    d = (destino or "principal").strip().lower()
    if d in ("principal", "isla", "main"):
        set_progress_value("base_level", str(nivel))
        set_progress_value("base_shulkers", str(total_shulkers_all_time()))
        set_progress_value("base_date", local_date_str())
        nombre = "Isla Principal"
    elif d in ("secundaria", "segunda", "sec"):
        set_progress_value("base_level_secundaria", str(nivel))
        set_progress_value("base_shulkers_secundaria", str(total_secundaria_all_time()))
        set_progress_value("base_date_secundaria", local_date_str())
        nombre = "Isla Secundaria"
    else:
        await ctx.reply(
            "❌ Destino inválido. Usa `principal` o `secundaria`.\n"
            "Ejemplo: `!setnivel 500000 secundaria`",
            mention_author=False
        )
        return

    await ctx.reply(
        f"✅ Nivel base de **{nombre}** fijado en `{nivel:,}` y progreso recalibrado.",
        mention_author=False
    )
    await actualizar_panel_progreso()


@bot.command(name="estadoisla")
@commands.has_permissions(administrator=True)
async def estadoisla(ctx, destino: str = "principal"):
    """Muestra el estado de una isla. Uso: !estadoisla [principal|secundaria]"""
    d = (destino or "principal").strip().lower()
    if d in ("principal", "isla", "main"):
        base_level = int(get_progress_value("base_level", "0") or 0)
        base_shulkers = int(get_progress_value("base_shulkers", "0") or 0)
        base_date = get_progress_value("base_date", "sin fecha")
        total_sh = total_shulkers_all_time()
        nombre = "Isla Principal"
        color = discord.Color.dark_teal()
    elif d in ("secundaria", "segunda", "sec"):
        base_level = int(get_progress_value("base_level_secundaria", "0") or 0)
        base_shulkers = int(get_progress_value("base_shulkers_secundaria", "0") or 0)
        base_date = get_progress_value("base_date_secundaria", "sin fecha")
        total_sh = total_secundaria_all_time()
        nombre = "Isla Secundaria"
        color = discord.Color.teal()
    else:
        await ctx.reply(
            "❌ Destino inválido. Usa `principal` o `secundaria`.",
            mention_author=False
        )
        return

    nuevos_sh = max(0, total_sh - base_shulkers)
    niveles_ganados = nuevos_sh * LEVELS_PER_SHULKER
    nivel_estimado = base_level + niveles_ganados

    embed = discord.Embed(
        title=f"📊 Estado actual — {nombre}",
        color=color,
        timestamp=utc_now()
    )
    embed.add_field(name="Base nivel", value=f"`{base_level:,}`", inline=False)
    embed.add_field(name="Base shulkers", value=f"`{base_shulkers:,}`", inline=True)
    embed.add_field(name="Shulkers totales", value=f"`{total_sh:,}`", inline=True)
    embed.add_field(name="Shulkers desde base", value=f"`{nuevos_sh:,}`", inline=True)
    embed.add_field(name="Niveles ganados", value=f"`{niveles_ganados:,}`", inline=True)
    embed.add_field(name="Nivel estimado", value=f"`{nivel_estimado:,}`", inline=True)
    embed.set_footer(text=f"Base tomada desde: {base_date} | Hora Chile")
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="publicarbotones")
@commands.has_permissions(administrator=True)
async def publicarbotones(ctx):
    await publicar_boton_shulker()
    await publicar_boton_end_aportada()
    await publicar_panel_estadisticas()
    await ctx.reply("✅ Botones/paneles republicados correctamente.", mention_author=False)

@bot.command(name="publicarrankings")
@commands.has_permissions(administrator=True)
async def publicarrankings(ctx):
    await actualizar_todos_los_ranking()
    await actualizar_rankings_end()
    await actualizar_rankings_secundaria()
    await actualizar_estado_evento()
    await ctx.reply("✅ Rankings republicados/actualizados correctamente.", mention_author=False)

@bot.command(name="publicarpanel")
@commands.has_permissions(administrator=True)
async def publicarpanel(ctx):
    await actualizar_panel_progreso()
    await ctx.reply("✅ Panel republicado/actualizado correctamente.", mention_author=False)

@bot.command(name="publicarestadisticas")
@commands.has_permissions(administrator=True)
async def publicarestadisticas(ctx):
    await publicar_panel_estadisticas()
    await ctx.reply("✅ Panel de estadísticas republicado correctamente.", mention_author=False)

@bot.command(name="sincronizarrevision")
@commands.has_permissions(administrator=True)
async def sincronizarrevision(ctx):
    await sincronizar_mensajes_revision()
    await ctx.reply("✅ Mensajes de revisión sincronizados.", mention_author=False)

@bot.command(name="stafflogs")
@commands.has_permissions(administrator=True)
async def stafflogs(ctx, limite: int = 10):
    limite = max(1, min(limite, 20))

    cursor.execute("""
        SELECT action, request_id, target_username, actor_username, amount, reason, created_at
        FROM staff_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limite,))
    rows = cursor.fetchall()

    if not rows:
        await ctx.reply("ℹ️ No hay logs de staff aún.", mention_author=False)
        return

    lines = []
    for r in rows:
        action_title, _ = get_staff_action_info(r["action"])
        texto = (
            f"• `{r['created_at']}` | **{action_title}**"
            f" | req `#{r['request_id'] or '-'}'"
            f" | usuario `{r['target_username'] or '-'}'"
            f" | staff `{r['actor_username'] or '-'}'"
            f" | cant `{r['amount'] if r['amount'] is not None else '-'}'"
        )
        if r["reason"]:
            texto += f"\n  motivo: {r['reason'][:120]}"
        lines.append(texto)

    embed = discord.Embed(
        title="🛡 Últimos logs de staff",
        description="\n".join(lines),
        color=discord.Color.dark_gold(),
        timestamp=utc_now()
    )
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="migrarevento")
@commands.has_permissions(administrator=True)
async def migrarevento(ctx):
    """Mueve registros de Isla Principal desde el 25 de julio 2026 al Evento y los borra de Principal."""
    start = str(EVENTO_START_DATE)

    cursor.execute("""
        SELECT user_id, username, fecha, total
        FROM shulker
        WHERE fecha >= ?
    """, (start,))
    rows = cursor.fetchall()

    if not rows:
        await ctx.reply("ℹ️ No hay registros desde el 25 de julio para migrar.", mention_author=False)
        return

    movidos = 0
    for r in rows:
        cursor.execute("""
            INSERT INTO shulker_evento (user_id, username, fecha, total)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, fecha)
            DO UPDATE SET
                total = total + excluded.total,
                username = excluded.username
        """, (r["user_id"], r["username"], r["fecha"], r["total"]))
        movidos += 1

    cursor.execute("DELETE FROM shulker WHERE fecha >= ?", (start,))
    db.commit()

    await actualizar_estado_evento()
    await actualizar_todos_los_ranking()

    await ctx.reply(
        f"✅ Migración completada.\n"
        f"Se movieron `{movidos}` registros (desde {start}) a **Evento**.\n"
        f"También se eliminaron de Isla Principal.",
        mention_author=False
    )

@bot.command(name="publicarrankingsnuevos")
@commands.has_permissions(administrator=True)
async def publicarrankingsnuevos(ctx):
    await actualizar_rankings_secundaria()
    await actualizar_estado_evento()
    await ctx.reply("✅ Rankings de Secundaria y Evento actualizados.", mention_author=False)

@bot.command(name="setmetaevento")
@commands.has_permissions(administrator=True)
async def setmetaevento(ctx, pvs: int):
    """Cambia la meta del evento en PVs (ejemplo: !setmetaevento 40)."""
    global EVENTO_GOAL_PVS, EVENTO_GOAL_SHULKERS
    if pvs <= 0:
        await ctx.reply("❌ La meta debe ser un número positivo.", mention_author=False)
        return
    EVENTO_GOAL_PVS = pvs
    EVENTO_GOAL_SHULKERS = pvs * SHULKERS_PER_PV
    guardar_config_evento()
    await actualizar_estado_evento()
    await ctx.reply(
        f"✅ Meta del evento actualizada a **{pvs} PVs** (`{EVENTO_GOAL_SHULKERS}` shulkers).",
        mention_author=False
    )


@bot.command(name="terminarevento")
@commands.has_permissions(administrator=True)
async def terminarevento(ctx):
    """Cierra el evento actual, bloquea registros (si aplica) y deja el panel final."""
    global EVENTO_STATUS

    if EVENTO_STATUS == "ended":
        await ctx.reply("ℹ️ El evento ya estaba terminado.", mention_author=False)
        await actualizar_estado_evento()
        return

    if EVENTO_STATUS != "active":
        await ctx.reply("ℹ️ No hay un evento activo para terminar.", mention_author=False)
        return

    ganador = get_evento_ganador() if EVENTO_TIPO == "end" else None
    EVENTO_STATUS = "ended"
    guardar_config_evento()
    await actualizar_estado_evento()

    embed = construir_embed_evento_terminado()
    await ctx.reply(
        content="🏁 **¡Evento cerrado!**",
        embed=embed,
        mention_author=False
    )

    try:
        ch = bot.get_channel(EVENTO_RANKING_CHANNEL_ID)
        if not ch:
            return
        if EVENTO_TIPO == "end" and ganador:
            g_name = ganador["username"]
            g_total = int(ganador["s"] or 0)
            await ch.send(
                f"🎉🎊 **¡EL EVENTO HA TERMINADO!** 🎊🎉\n"
                f"🏆 Felicitamos a **{g_name}** por ganar **{EVENTO_NOMBRE}** "
                f"con `{g_total}` shulkers.\n"
                f"👏 Gracias a todos los que aportaron."
            )
        else:
            await ch.send(
                f"🎉🎊 **¡EL EVENTO HA TERMINADO!** 🎊🎉\n"
                f"🏁 **{EVENTO_NOMBRE}**\n"
                f"Tipo: {_label_tipo_evento()}\n"
                f"Los ganadores se definen según el ranking del juego / staff.\n"
                f"👏 Gracias a todos por participar."
            )
    except Exception as e:
        print(f"⚠️ No se pudo enviar anuncio de fin de evento: {e}")


async def _activar_evento_desde_modal(
    interaction: discord.Interaction,
    *,
    tipo: str,
    nombre: str,
    fecha_inicio: str,
    objetivo: str,
    recompensas: str,
    reglas: str,
    meta_pvs: int | None = None,
):
    global EVENTO_STATUS, EVENTO_NOMBRE, EVENTO_GOAL_PVS, EVENTO_GOAL_SHULKERS
    global EVENTO_START_DATE, EVENTO_RECOMPENSAS, EVENTO_REGLAS, EVENTO_PARTICIPANTES
    global EVENTO_TIPO, EVENTO_OBJETIVO

    try:
        inicio = date.fromisoformat(fecha_inicio.strip())
    except ValueError:
        await interaction.followup.send(
            "❌ Fecha inválida. Usa `YYYY-MM-DD` (ejemplo: `2026-08-05`).",
            ephemeral=True
        )
        return

    EVENTO_TIPO = tipo
    EVENTO_NOMBRE = nombre.strip()
    EVENTO_START_DATE = inicio
    EVENTO_OBJETIVO = (objetivo or "").strip()
    EVENTO_RECOMPENSAS = (recompensas or "").strip()
    EVENTO_REGLAS = (reglas or "").strip()
    EVENTO_PARTICIPANTES = EVENTO_REGLAS[:200] if EVENTO_REGLAS else "Todo el team"

    if tipo == "end":
        if not meta_pvs or meta_pvs <= 0:
            await interaction.followup.send("❌ Meta de PVs inválida.", ephemeral=True)
            return
        EVENTO_GOAL_PVS = meta_pvs
        EVENTO_GOAL_SHULKERS = meta_pvs * SHULKERS_PER_PV
        if not EVENTO_OBJETIVO:
            EVENTO_OBJETIVO = f"Colocar End hasta completar {meta_pvs} PVs"
    else:
        # No aplica meta de End
        EVENTO_GOAL_PVS = 0
        EVENTO_GOAL_SHULKERS = 0

    EVENTO_STATUS = "active"
    guardar_config_evento()
    await actualizar_estado_evento()

    extra = (
        f"📅 Desde `{EVENTO_START_DATE}` | Meta: `{EVENTO_GOAL_PVS}` PVs\n"
        f"Opción **Evento** habilitada en el menú de registro."
        if tipo == "end"
        else f"📅 Desde `{EVENTO_START_DATE}`\nSin registro de shulkers (se mide en el juego / staff)."
    )
    await interaction.followup.send(
        f"✅ Evento **{EVENTO_NOMBRE}** creado y **activado**.\n"
        f"Tipo: {_label_tipo_evento(tipo)}\n{extra}",
        ephemeral=True
    )

    try:
        ch = bot.get_channel(EVENTO_RANKING_CHANNEL_ID)
        if ch:
            hint = (
                "Usa el botón de registrar shulker → opción **Evento**."
                if tipo == "end"
                else "Sigue las reglas del evento; el progreso se mide en el juego."
            )
            await ch.send(
                content=f"📢 **¡NUEVO EVENTO!**\n🎉 **{EVENTO_NOMBRE}**\n{hint}",
                embed=construir_embed_evento_activo()
            )
    except Exception as e:
        print(f"⚠️ No se pudo anunciar el nuevo evento: {e}")


class CrearEventoEndModal(discord.ui.Modal, title="Evento: End / Shulkers"):
    nombre = discord.ui.TextInput(
        label="Nombre del evento",
        placeholder="Ej: Evento 50 PVs de End",
        max_length=80,
        required=True
    )
    meta_pvs = discord.ui.TextInput(
        label="Meta en PVs (número)",
        placeholder="Ej: 50",
        max_length=6,
        required=True
    )
    fecha_inicio = discord.ui.TextInput(
        label="Fecha de inicio (YYYY-MM-DD)",
        placeholder="Ej: 2026-08-05",
        max_length=10,
        required=True
    )
    recompensas = discord.ui.TextInput(
        label="Recompensas / premios",
        style=discord.TextStyle.paragraph,
        placeholder="Top 1: Tag custom | Top 2-3: kit...",
        max_length=500,
        required=False
    )
    reglas = discord.ui.TextInput(
        label="Reglas y quién puede participar",
        style=discord.TextStyle.paragraph,
        placeholder="Todo el team. Solo End del evento...",
        max_length=500,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            pvs = int(str(self.meta_pvs.value).strip())
        except ValueError:
            await interaction.followup.send("❌ Meta de PVs inválida.", ephemeral=True)
            return
        await _activar_evento_desde_modal(
            interaction,
            tipo="end",
            nombre=str(self.nombre.value),
            fecha_inicio=str(self.fecha_inicio.value),
            objetivo=f"Colocar End hasta completar {pvs} PVs",
            recompensas=str(self.recompensas.value or ""),
            reglas=str(self.reglas.value or ""),
            meta_pvs=pvs,
        )


class CrearEventoRankingModal(discord.ui.Modal, title="Evento: Ranking del juego"):
    nombre = discord.ui.TextInput(
        label="Nombre del evento",
        placeholder="Ej: Battle Pass — Top matar mobs",
        max_length=80,
        required=True
    )
    objetivo = discord.ui.TextInput(
        label="Qué se compite / cómo se gana",
        style=discord.TextStyle.paragraph,
        placeholder="Top 10 matar mobs / minar bloques del battle pass...",
        max_length=400,
        required=True
    )
    fecha_inicio = discord.ui.TextInput(
        label="Fecha de inicio (YYYY-MM-DD)",
        placeholder="Ej: 2026-08-05",
        max_length=10,
        required=True
    )
    recompensas = discord.ui.TextInput(
        label="Recompensas según puesto",
        style=discord.TextStyle.paragraph,
        placeholder="Top 1: X | Top 2-3: Y | Top 4-10: Z",
        max_length=500,
        required=False
    )
    reglas = discord.ui.TextInput(
        label="Reglas y quién puede participar",
        style=discord.TextStyle.paragraph,
        placeholder="Todo el team. Solo cuenta el top del pase...",
        max_length=500,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await _activar_evento_desde_modal(
            interaction,
            tipo="ranking",
            nombre=str(self.nombre.value),
            fecha_inicio=str(self.fecha_inicio.value),
            objetivo=str(self.objetivo.value),
            recompensas=str(self.recompensas.value or ""),
            reglas=str(self.reglas.value or ""),
        )


class CrearEventoCustomModal(discord.ui.Modal, title="Evento personalizado"):
    nombre = discord.ui.TextInput(
        label="Nombre del evento",
        placeholder="Ej: Concurso de builds",
        max_length=80,
        required=True
    )
    objetivo = discord.ui.TextInput(
        label="Objetivo del evento",
        style=discord.TextStyle.paragraph,
        placeholder="Qué hay que hacer para ganar...",
        max_length=400,
        required=True
    )
    fecha_inicio = discord.ui.TextInput(
        label="Fecha de inicio (YYYY-MM-DD)",
        placeholder="Ej: 2026-08-05",
        max_length=10,
        required=True
    )
    recompensas = discord.ui.TextInput(
        label="Recompensas",
        style=discord.TextStyle.paragraph,
        placeholder="Premios del evento...",
        max_length=500,
        required=False
    )
    reglas = discord.ui.TextInput(
        label="Reglas y participantes",
        style=discord.TextStyle.paragraph,
        placeholder="Quién puede unirse y reglas...",
        max_length=500,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await _activar_evento_desde_modal(
            interaction,
            tipo="custom",
            nombre=str(self.nombre.value),
            fecha_inicio=str(self.fecha_inicio.value),
            objetivo=str(self.objetivo.value),
            recompensas=str(self.recompensas.value or ""),
            reglas=str(self.reglas.value or ""),
        )


class TipoEventoSelect(discord.ui.Select):
    def __init__(self, author_id: int):
        self.author_id = author_id
        options = [
            discord.SelectOption(
                label="End / Shulkers",
                value="end",
                emoji="🪨",
                description="Meta en PVs + registro en el bot"
            ),
            discord.SelectOption(
                label="Ranking del juego",
                value="ranking",
                emoji="🎮",
                description="Battle pass, tops mobs/minería, etc."
            ),
            discord.SelectOption(
                label="Personalizado",
                value="custom",
                emoji="⭐",
                description="Cualquier otro tipo de evento del team"
            ),
        ]
        super().__init__(
            placeholder="¿Qué tipo de evento quieres crear?",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Solo quien ejecutó el comando puede usar esto.",
                ephemeral=True
            )
            return
        tipo = self.values[0]
        if tipo == "end":
            await interaction.response.send_modal(CrearEventoEndModal())
        elif tipo == "ranking":
            await interaction.response.send_modal(CrearEventoRankingModal())
        else:
            await interaction.response.send_modal(CrearEventoCustomModal())


class CrearEventoView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.add_item(TipoEventoSelect(author_id))


@bot.command(name="crearevento")
@commands.has_permissions(administrator=True)
async def crearevento(ctx):
    """Elige el tipo de evento y completa el formulario."""
    await ctx.send(
        "📋 **Crear evento** — elige el tipo:",
        view=CrearEventoView(ctx.author.id),
        delete_after=180
    )


# ===============================
# COMANDOS STOCK ALMACÉN
# ===============================
@bot.command(name="stock")
@commands.has_permissions(administrator=True)
async def stock_cmd(ctx):
    """Muestra el stock actual de End en almacén."""
    sp = get_stock_shulkers("principal")
    ss = get_stock_shulkers("secundaria")
    total = sp + ss
    embed = discord.Embed(
        title="📦 Stock de End — Almacén",
        color=discord.Color.dark_blue(),
        timestamp=utc_now()
    )
    embed.add_field(name="🏝️ Principal", value=format_stock_line("Stock", sp).replace("**Stock:** ", ""), inline=False)
    embed.add_field(name="🌿 Secundaria", value=format_stock_line("Stock", ss).replace("**Stock:** ", ""), inline=False)
    tpv, tr = shulkers_a_pv_y_shulkers(total)
    embed.add_field(name="Σ Total", value=f"`{total:,}` shulkers (`{tpv}` PVs + `{tr}`)", inline=False)
    embed.set_footer(text="!stocksumar | !stockquitar | !stockset | !publicarstock")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="stocksumar")
@commands.has_permissions(administrator=True)
async def stocksumar(ctx, destino: str, cantidad: int, unidad: str = "shulker"):
    """
    Suma End al almacén.
    Uso: !stocksumar principal 540
         !stocksumar secundaria 20 pv
    """
    if _stock_key(destino) is None:
        await ctx.reply("❌ Destino: `principal` o `secundaria`.", mention_author=False)
        return
    if cantidad <= 0:
        await ctx.reply("❌ La cantidad debe ser > 0.", mention_author=False)
        return
    try:
        sh = parse_cantidad_stock(cantidad, unidad)
    except ValueError:
        await ctx.reply("❌ Unidad inválida. Usa `shulker` o `pv`.", mention_author=False)
        return
    nuevo = add_stock_shulkers(destino, sh)
    await ctx.reply(
        f"✅ `+{sh}` shulkers a **{destino}**.\n"
        f"📦 Stock actual: {format_stock_line(destino, nuevo)}",
        mention_author=False
    )
    await actualizar_panel_stock()


@bot.command(name="stockquitar")
@commands.has_permissions(administrator=True)
async def stockquitar(ctx, destino: str, cantidad: int, unidad: str = "shulker"):
    """
    Quita End del almacén (no baja de 0).
    Uso: !stockquitar principal 27
         !stockquitar secundaria 1 pv
    """
    if _stock_key(destino) is None:
        await ctx.reply("❌ Destino: `principal` o `secundaria`.", mention_author=False)
        return
    if cantidad <= 0:
        await ctx.reply("❌ La cantidad debe ser > 0.", mention_author=False)
        return
    try:
        sh = parse_cantidad_stock(cantidad, unidad)
    except ValueError:
        await ctx.reply("❌ Unidad inválida. Usa `shulker` o `pv`.", mention_author=False)
        return
    restado, nuevo = remove_stock_shulkers(destino, sh)
    await ctx.reply(
        f"✅ `-{restado}` shulkers de **{destino}** "
        f"(pediste quitar `{sh}`).\n"
        f"📦 Stock actual: {format_stock_line(destino, nuevo)}",
        mention_author=False
    )
    await actualizar_panel_stock()


@bot.command(name="stockset")
@commands.has_permissions(administrator=True)
async def stockset(ctx, destino: str, cantidad: int, unidad: str = "shulker"):
    """
    Fija el stock exacto de una isla.
    Uso: !stockset principal 1080
         !stockset secundaria 20 pv
    """
    if _stock_key(destino) is None:
        await ctx.reply("❌ Destino: `principal` o `secundaria`.", mention_author=False)
        return
    if cantidad < 0:
        await ctx.reply("❌ La cantidad no puede ser negativa.", mention_author=False)
        return
    try:
        sh = parse_cantidad_stock(cantidad, unidad)
    except ValueError:
        await ctx.reply("❌ Unidad inválida. Usa `shulker` o `pv`.", mention_author=False)
        return
    nuevo = set_stock_shulkers(destino, sh)
    await ctx.reply(
        f"✅ Stock de **{destino}** fijado.\n"
        f"📦 {format_stock_line(destino, nuevo)}",
        mention_author=False
    )
    await actualizar_panel_stock()


@bot.command(name="publicarstock")
@commands.has_permissions(administrator=True)
async def publicarstock(ctx):
    """Actualiza el panel de stock en el canal configurado (STOCK_CHANNEL_ID)."""
    if not STOCK_CHANNEL_ID:
        await ctx.reply(
            "⚠️ `STOCK_CHANNEL_ID` está en `0`. Pon el ID del canal staff en el código y reinicia.\n"
            "Mientras tanto puedes usar `!stock`.",
            mention_author=False
        )
        return
    await actualizar_panel_stock()
    await ctx.reply("✅ Panel de stock actualizado.", mention_author=False)


# ===============================
# COMANDOS CUMPLEAÑOS
# ===============================
@bot.command(name="micumple")
async def micumple(ctx, fecha: str):
    """Registra tu cumpleaños (solo una vez). Mejor usa el botón del canal. Uso: !micumple 15/03"""
    if get_birthday(ctx.author.id):
        row = get_birthday(ctx.author.id)
        await ctx.reply(
            f"⚠️ Ya registraste tu cumpleaños "
            f"(**{int(row['day']):02d}/{int(row['month']):02d}**).\n"
            f"No puedes cambiarlo tú. Si está mal: staff `!setcumple @tú DD/MM`.",
            mention_author=False
        )
        return
    parsed = parse_cumple_ddmm(fecha)
    if not parsed:
        await ctx.reply("❌ Fecha inválida. Usa `DD/MM` (ej: `!micumple 15/03`).", mention_author=False)
        return
    day, month = parsed
    set_birthday(ctx.author.id, ctx.author.display_name, day, month)
    dias = days_until_birthday(day, month)
    extra = "¡**Es hoy**! 🎉" if dias == 0 else ("¡Es **mañana**!" if dias == 1 else f"Faltan **{dias}** días.")
    await ctx.reply(
        f"🎂 Guardado: **{day:02d} de {MESES_ES[month]}**.\n{extra}",
        mention_author=False
    )
    await actualizar_panel_cumpleanos()


@bot.command(name="setcumple")
@commands.has_permissions(administrator=True)
async def setcumple(ctx, member: discord.Member, fecha: str):
    """Staff: !setcumple @user 15/03"""
    parsed = parse_cumple_ddmm(fecha)
    if not parsed:
        await ctx.reply("❌ Fecha inválida. Usa `DD/MM`.", mention_author=False)
        return
    day, month = parsed
    set_birthday(member.id, member.display_name, day, month)
    await ctx.reply(
        f"✅ Cumpleaños de {member.mention}: **{day:02d}/{month:02d}** ({day} de {MESES_ES[month]}).",
        mention_author=False
    )
    await actualizar_panel_cumpleanos()


@bot.command(name="quitarcumple")
@commands.has_permissions(administrator=True)
async def quitarcumple(ctx, member: discord.Member):
    """Staff: elimina el cumpleaños de un miembro."""
    cursor.execute("DELETE FROM birthdays WHERE user_id = ?", (member.id,))
    db.commit()
    await ctx.reply(f"🗑️ Cumpleaños de {member.mention} eliminado.", mention_author=False)
    await actualizar_panel_cumpleanos()


@bot.command(name="cumples")
async def cumples(ctx):
    """Muestra la lista de cumpleaños (próximos primero)."""
    await ctx.reply(embed=construir_embed_lista_cumples_ephemeral(), mention_author=False)


@bot.command(name="publicarcumples")
@commands.has_permissions(administrator=True)
async def publicarcumples(ctx):
    """Publica/actualiza el panel con botones en el canal de cumpleaños."""
    if not BIRTHDAY_CHANNEL_ID:
        await ctx.reply(
            "⚠️ `BIRTHDAY_CHANNEL_ID` está en `0`. Pon el ID del canal y reinicia.\n"
            "Preview de la lista:",
            mention_author=False
        )
        await ctx.send(embed=construir_embed_lista_cumples_ephemeral())
        return
    await actualizar_panel_cumpleanos()
    await anunciar_cumpleanos_del_dia()
    await ctx.reply("✅ Panel de cumpleaños actualizado.", mention_author=False)


# ===============================
# COMANDOS FUNCIONES DEL TEAM
# ===============================
@bot.command(name="publicarfunciones")
@commands.has_permissions(administrator=True)
async def publicarfunciones(ctx):
    """Publica/actualiza el panel de funciones (Minero/Obrero/Constructor)."""
    await actualizar_panel_funciones()
    await ctx.reply("✅ Panel de funciones actualizado.", mention_author=False)


@bot.command(name="funciones")
@commands.has_permissions(administrator=True)
async def funciones_cmd(ctx):
    """Lista miembros por función del team."""
    embed = discord.Embed(
        title="📋 Miembros por función",
        color=discord.Color.blurple(),
        timestamp=utc_now()
    )
    for key, (_, emoji, label, desc) in FUNCIONES_TEAM.items():
        rows = list_users_by_function(key)
        if not rows:
            valor = "_Nadie aún_"
        else:
            valor = "\n".join(
                f"• <@{r['user_id']}>" for r in rows[:40]
            )
            if len(rows) > 40:
                valor += f"\n_… y {len(rows) - 40} más_"
        embed.add_field(
            name=f"{emoji} {label} ({len(rows)})",
            value=valor[:1024],
            inline=False
        )
    embed.set_footer(text=desc if False else "Multi-rol · Hora Chile")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="misfunciones")
async def misfunciones(ctx):
    """Muestra tus funciones activas en el team."""
    activas = get_user_functions(ctx.author.id)
    if not activas:
        await ctx.reply(
            "No tienes funciones asignadas. Ve al canal de funciones y elige con los botones.",
            mention_author=False
        )
        return
    nombres = []
    for k in activas:
        e, lab, d = FUNCIONES_TEAM[k][1], FUNCIONES_TEAM[k][2], FUNCIONES_TEAM[k][3]
        nombres.append(f"{e} **{lab}** — _{d}_")
    await ctx.reply(
        "**Tus funciones:**\n" + "\n".join(nombres),
        mention_author=False
    )


@bot.command(name="miminimo")
async def miminimo(ctx):
    """Progreso de mínimos de esta semana (End aportada / colocada)."""
    uid = ctx.author.id
    wid = current_week_id()
    ini, fin = week_date_bounds(wid)

    embed = discord.Embed(
        title="📊 Tu mínimo semanal",
        description=f"Semana **{wid}** (`{ini}` → `{fin}`)",
        color=discord.Color.orange(),
        timestamp=utc_now()
    )

    if is_member_restricted(uid):
        embed.add_field(
            name="🚫 Estado",
            value="**RESTRINGIDO** — habla con staff",
            inline=False
        )

    for funcion, titulo, tipo in (
        ("minero", "⛏️ Minero (aportada)", "aportada"),
        ("obrero", "🧱 Obrero (colocada)", "colocada"),
    ):
        actual, minimo = progreso_semana_funcion(uid, funcion, wid)
        pv_a, sh_a = shulkers_a_pv_y_shulkers(actual)
        ok = actual >= minimo
        strikes = get_strikes(uid, funcion)
        embed.add_field(
            name=titulo,
            value=(
                f"{'✅' if ok else '⏳'} `{actual}` / `{minimo}` shulkers "
                f"(`{pv_a}` PVs + `{sh_a}`)\n"
                f"Strikes: **{strikes}/{MAX_STRIKES_FUNCION}**"
            ),
            inline=False
        )

    activas = get_user_functions(uid)
    embed.add_field(
        name="📋 Funciones activas",
        value=", ".join(
            f"{FUNCIONES_TEAM[k][1]} {FUNCIONES_TEAM[k][2]}" for k in activas
        ) if activas else "_Ninguna_",
        inline=False
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="evaluarminimos")
@commands.has_permissions(administrator=True)
async def evaluarminimos(ctx):
    """Fuerza la evaluación de la semana anterior (admin)."""
    semana = previous_week_id()
    # permitir re-ejecutar borrando flag
    cursor.execute("DELETE FROM island_progress WHERE key = ?", (f"minimos_evaluados_{semana}",))
    db.commit()
    await ctx.reply(f"⏳ Evaluando mínimos de `{semana}`…", mention_author=False)
    await evaluar_minimos_semana_anterior()
    await ctx.reply("✅ Evaluación terminada (revisa logs / MDs enviados).", mention_author=False)


@bot.command(name="repararminimos")
@commands.has_permissions(administrator=True)
async def repararminimos(ctx):
    """
    REPARACIÓN: borra strikes/restricciones por el falso positivo,
    marca semanas viejas como omitidas y envía disculpa por MD.
    Uso: !repararminimos
    """
    await ctx.reply(
        "⏳ Reparando strikes, quitando restricciones y enviando disculpas…",
        mention_author=False
    )

    # Usuarios que tenían strikes o estaban restringidos
    cursor.execute("""
        SELECT DISTINCT user_id FROM function_strikes WHERE strikes > 0
    """)
    ids_strikes = {int(r["user_id"]) for r in cursor.fetchall()}

    cursor.execute("""
        SELECT user_id FROM member_status WHERE status = 'restricted'
    """)
    ids_rest = {int(r["user_id"]) for r in cursor.fetchall()}

    afectados = ids_strikes | ids_rest

    # Reset total de strikes
    cursor.execute("UPDATE function_strikes SET strikes = 0, last_fail_week = NULL, updated_at = ?",
                   (local_datetime_str(),))
    # Quitar restringidos
    cursor.execute("""
        UPDATE member_status
        SET status = 'active', restricted_at = NULL, reason = NULL
        WHERE status = 'restricted'
    """)
    # Marcar semanas evaluadas antes del lanzamiento como omitidas (no volver a castigar)
    for wid in (previous_week_id(), current_week_id(), week_id_from_date(today_local() - timedelta(days=14))):
        ini, fin = week_date_bounds(wid)
        if date.fromisoformat(fin) < MINIMOS_ACTIVE_FROM:
            set_progress_value(
                f"minimos_evaluados_{wid}",
                f"skipped_before_{MINIMOS_ACTIVE_FROM}"
            )
        else:
            # borrar flag de la semana actual de evaluación errónea reciente
            cursor.execute(
                "DELETE FROM island_progress WHERE key = ?",
                (f"minimos_evaluados_{wid}",)
            )
    # Forzar omitir explícitamente W31 y similares recientes si terminaron antes del 10 ago
    for delta in range(0, 21):
        wid = week_id_from_date(today_local() - timedelta(days=delta))
        _, fin = week_date_bounds(wid)
        if date.fromisoformat(fin) < MINIMOS_ACTIVE_FROM:
            set_progress_value(
                f"minimos_evaluados_{wid}",
                f"skipped_before_{MINIMOS_ACTIVE_FROM}"
            )
    db.commit()

    disculpa = (
        "🙏 **Disculpas — Endcore Team / Shulker Tracker**\n\n"
        "La advertencia de **mínimo semanal** que recibiste fue un **error del sistema** "
        "(se evaluó una semana anterior al inicio oficial de los mínimos).\n\n"
        "✅ Tus **strikes quedaron en 0**\n"
        "✅ **No** estás restringido por ese aviso\n"
        "✅ Puedes **volver a elegir** tu rol (Minero / Obrero / Constructor) y empezar de cero\n\n"
        f"Los mínimos reales empiezan a contar desde el **{MINIMOS_ACTIVE_FROM.strftime('%d/%m/%Y')}**.\n"
        "Lamentamos la confusión."
    )

    enviados = 0
    fallidos = 0
    for uid in afectados:
        try:
            user = await bot.fetch_user(uid)
            await user.send(disculpa)
            enviados += 1
            await asyncio.sleep(0.4)
        except Exception:
            fallidos += 1

    # Si no había registros de strikes pero igual quieres avisar a quienes tienen función
    if not afectados:
        cursor.execute("SELECT DISTINCT user_id FROM member_functions")
        extra = [int(r["user_id"]) for r in cursor.fetchall()]
        for uid in extra:
            try:
                user = await bot.fetch_user(uid)
                await user.send(disculpa)
                enviados += 1
                await asyncio.sleep(0.4)
            except Exception:
                fallidos += 1

    await actualizar_panel_funciones()
    await ctx.reply(
        f"✅ Reparación lista.\n"
        f"· Strikes reseteados a 0\n"
        f"· Restricciones quitadas\n"
        f"· Semanas previas a `{MINIMOS_ACTIVE_FROM}` marcadas como omitidas\n"
        f"· Disculpas enviadas: `{enviados}` · fallidas (MD cerrado): `{fallidos}`\n\n"
        f"Los miembros ya pueden volver a elegir rol en el canal de funciones.",
        mention_author=False
    )


@bot.command(name="strikes")
@commands.has_permissions(administrator=True)
async def strikes_cmd(ctx, member: discord.Member):
    """Ver strikes de mínimos de un miembro."""
    lines = []
    for funcion in ("minero", "obrero"):
        row = get_strike_row(member.id, funcion)
        s = int(row["strikes"] or 0) if row else 0
        lf = row["last_fail_week"] if row else "—"
        lo = row["last_ok_week"] if row else "—"
        lines.append(
            f"{FUNCIONES_TEAM[funcion][1]} **{FUNCIONES_TEAM[funcion][2]}**: "
            f"`{s}/{MAX_STRIKES_FUNCION}` · fail `{lf}` · ok `{lo}`"
        )
    estado = "🚫 RESTRINGIDO" if is_member_restricted(member.id) else "✅ Activo"
    await ctx.reply(
        f"Strikes de {member.mention}:\n" + "\n".join(lines) + f"\nEstado: {estado}",
        mention_author=False
    )


@bot.command(name="resetstrikes")
@commands.has_permissions(administrator=True)
async def resetstrikes(ctx, member: discord.Member, funcion: str = "all"):
    """Resetea strikes. Uso: !resetstrikes @user [minero|obrero|all]"""
    f = (funcion or "all").lower()
    targets = ["minero", "obrero"] if f == "all" else [f]
    for t in targets:
        if t not in ("minero", "obrero"):
            await ctx.reply("❌ Función: `minero`, `obrero` o `all`.", mention_author=False)
            return
        set_strikes(member.id, t, 0)
    await ctx.reply(f"✅ Strikes reseteados para {member.mention} (`{f}`).", mention_author=False)


@bot.command(name="restringidos")
@commands.has_permissions(administrator=True)
async def restringidos_cmd(ctx):
    """Lista usuarios restringidos por mínimos."""
    cursor.execute("""
        SELECT user_id, username, restricted_at, reason
        FROM member_status
        WHERE status = 'restricted'
        ORDER BY restricted_at DESC
    """)
    rows = cursor.fetchall()
    if not rows:
        await ctx.reply("ℹ️ No hay usuarios restringidos.", mention_author=False)
        return
    lines = [
        f"• <@{r['user_id']}> — `{r['restricted_at']}` — {r['reason'] or '—'}"
        for r in rows[:30]
    ]
    await ctx.reply("🚫 **Restringidos:**\n" + "\n".join(lines), mention_author=False)


@bot.command(name="quitarrestriccion")
@commands.has_permissions(administrator=True)
async def quitarrestriccion(ctx, member: discord.Member):
    """Quita el estado restringido de un miembro."""
    clear_member_restricted(member.id)
    if ROLE_RESTRINGIDO_ID and ctx.guild:
        rol = ctx.guild.get_role(ROLE_RESTRINGIDO_ID)
        if rol and rol in member.roles:
            try:
                await member.remove_roles(rol, reason="Restricción removida por staff")
            except Exception:
                pass
    for funcion in ("minero", "obrero"):
        set_strikes(member.id, funcion, 0)
    await ctx.reply(
        f"✅ {member.mention} ya no está restringido (strikes en 0).",
        mention_author=False
    )


@bot.command(name="forzarquitarfuncion")
@commands.has_permissions(administrator=True)
async def forzarquitarfuncion(ctx, member: discord.Member, funcion: str):
    """
    Quita una función ignorando el candado de 8 días.
    Uso: !forzarquitarfuncion @user minero|obrero|constructor
    """
    f = (funcion or "").strip().lower()
    if f not in FUNCIONES_TEAM:
        await ctx.reply("❌ Función: `minero`, `obrero` o `constructor`.", mention_author=False)
        return

    role_id, emoji, label, _ = FUNCIONES_TEAM[f]
    set_user_function(member.id, member.display_name, f, False)
    role = ctx.guild.get_role(role_id) if ctx.guild else None
    if role and role in member.roles:
        try:
            await member.remove_roles(role, reason=f"Staff forzó quitar {label}")
        except Exception as e:
            await ctx.reply(f"⚠️ DB actualizada pero no pude quitar el rol Discord: `{e}`", mention_author=False)
            return

    await ctx.reply(
        f"✅ Se quitó **{emoji} {label}** a {member.mention} (candado ignorado).",
        mention_author=False
    )
    await actualizar_panel_funciones()


# ===============================
# READY
# ===============================
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")
    print(f"📂 DB: {DB_PATH}")
    print(f"📌 Start date: {BOT_START_DATE}")
    print(f"🕒 Hora Chile: {local_datetime_str()}")
    print(f"✅ LEVELS_PER_SHULKER: {LEVELS_PER_SHULKER} | LEVELS_PER_PV: {LEVELS_PER_PV}")

    cargar_config_evento()
    print(
        f"🎉 Evento: [{EVENTO_STATUS}] {EVENTO_NOMBRE} | "
        f"meta {EVENTO_GOAL_PVS} PVs ({EVENTO_GOAL_SHULKERS} shulkers) desde {EVENTO_START_DATE}"
    )
    print("✅ BOT VERSION CON EVENTOS + 2 ISLAS CARGADA")

    cleanup_expired_cooldowns()
    cleanup_expired_pending_end()
    asegurar_base_progreso_si_falta()

    if not ranking_automatico.is_running():
        ranking_automatico.start()

    await publicar_boton_shulker()
    await publicar_boton_end_aportada()
    await publicar_panel_estadisticas()
    await actualizar_todos_los_ranking()
    await actualizar_panel_progreso()
    await actualizar_rankings_end()
    await actualizar_rankings_secundaria()
    await actualizar_estado_evento()
    await sincronizar_mensajes_revision()
    await actualizar_panel_stock()
    await actualizar_panel_cumpleanos()
    await anunciar_cumpleanos_del_dia()
    await actualizar_panel_funciones()
    await evaluar_minimos_semana_anterior()

# ===============================
# ERROR GLOBAL
# ===============================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ No tienes permisos para usar este comando.", mention_author=False)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("❌ Faltan argumentos para ese comando.", mention_author=False)
        return

    if isinstance(error, commands.BadArgument):
        await ctx.reply("❌ Argumento inválido.", mention_author=False)
        return

    print(f"❌ Error de comando: {error}")
    try:
        await ctx.reply(f"❌ Ocurrió un error al ejecutar el comando: `{error}`", mention_author=False)
    except Exception:
        pass

# ===============================
# RUN
# ===============================
if not TOKEN:
    raise ValueError("Falta la variable de entorno DISCORD_TOKEN")

bot.run(TOKEN)
