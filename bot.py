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
OLD_DB_PATH = "shulker.db"  # si existe en la raíz

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
    """
    Fecha desde la cual el bot contará todo (ignorará lo anterior).
    Se guarda una sola vez al iniciar: primer día del mes actual.
    """
    cursor.execute("SELECT value FROM bot_config WHERE key = 'start_date'")
    row = cursor.fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0])

    start = date.today().replace(day=1)
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('start_date', ?)", (str(start),))
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

    # Consolidamos por día (por si hubo duplicados)
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
    # Confirmado por ti:
    # 1 shulker = 27 stacks
    # 1 stack = 64 bloques end
    # 1 bloque end = 9 niveles isla
    # 1 PV = 27 shulkers
    stacks = total_shulkers * 27
    bloques = stacks * 64
    niveles = bloques * 9
    pv = total_shulkers // 27
    resto = total_shulkers % 27
    return stacks, bloques, niveles, pv, resto

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
# CREAR EMBED RANKING (MEJORADO)
# ===============================
async def crear_embed_ranking(titulo, emoji, color, datos, footer, total_periodo: int):
    # Ranking (solo top 10 visible, pero el total es real del periodo)
    descripcion = ""
    if datos:
        descripcion += "🏅 **Ranking**\n"
        for i, (user, total) in enumerate(datos, start=1):
            medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
            descripcion += f"{medalla} **{i}. {user}** — `{format_number(total)}` shulker\n"
    else:
        descripcion = "_Sin registros aún_"

    stacks, bloques, niveles, pv, resto = equivalencias(total_periodo)

    embed = discord.Embed(
        title=f"{emoji} {titulo}",
        description=descripcion,
        color=color
    )

    # Equivalencias como resumen total (no por persona)
    eq = (
        f"📦 **Total:** `{format_number(total_periodo)}` shulkers\n"
        f"📚 **Stacks:** `{format_number(stacks)}`\n"
        f"🧱 **Bloques End:** `{format_number(bloques)}`\n"
        f"📈 **Niveles Isla:** `{format_number(niveles)}`\n"
        f"⚖ **Equivalente:** `{pv}` PV + `{resto}` shulkers"
    )
    embed.add_field(name="📊 Equivalencias (total del período)", value=eq, inline=False)

    embed.set_footer(text=footer)
    return embed

# ===============================
# HELPERS: QUERIES CON "EMPEZAR ESTE MES"
# ===============================
def clamp_start(d: date) -> date:
    return d if d >= BOT_START_DATE else BOT_START_DATE

def total_periodo(where_sql: str, params: tuple) -> int:
    cursor.execute(f"SELECT COALESCE(SUM(total), 0) FROM shulker WHERE {where_sql}", params)
    return int(cursor.fetchone()[0] or 0)

# ===============================
# ACTUALIZAR RANKINGS (EDITA, NO SPAM)
# ===============================
async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    hoy = date.today()

    # Desde este mes en adelante (ignora todo antes)
    inicio_mes = clamp_start(hoy.replace(day=1))

    # Semana (pero nunca antes del start_date)
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_semana = clamp_start(inicio_semana)

    # -------------------------------
    # Ranking mensual (top 10 visible)
    # -------------------------------
    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 10
    """, (str(inicio_mes),))
    mensual = cursor.fetchall()

    total_mensual = total_periodo("fecha >= ?", (str(inicio_mes),))

    # -------------------------------
    # Ranking semanal (top 10 visible)
    # -------------------------------
    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 10
    """, (str(inicio_semana),))
    semanal = cursor.fetchall()

    total_semanal = total_periodo("fecha >= ?", (str(inicio_semana),))

    # -------------------------------
    # Ranking diario (top 10 visible)
    # -------------------------------
    # (si hoy es antes del start_date, se verá vacío)
    cursor.execute("""
        SELECT username, SUM(total) as s
        FROM shulker
        WHERE fecha = ? AND fecha >= ?
        GROUP BY user_id
        ORDER BY s DESC
        LIMIT 10
    """, (str(hoy), str(BOT_START_DATE)))
    diario = cursor.fetchall()

    total_diario = total_periodo("fecha = ? AND fecha >= ?", (str(hoy), str(BOT_START_DATE)))

    embeds = [
        await crear_embed_ranking(
            "TOP MENSUAL", "👑", discord.Color.purple(),
            mensual, "🗓️ Mes actual (desde el inicio del bot)", total_mensual
        ),
        await crear_embed_ranking(
            "TOP SEMANAL", "📈", discord.Color.blue(),
            semanal, f"📅 Semana actual • Desde {inicio_semana}", total_semanal
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

        # ✅ UPSERT ATÓMICO
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

    # Actualiza rankings al iniciar
    await actualizar_todos_los_ranking()

# ===============================
# INICIAR BOT
# ===============================
bot.run(TOKEN)
