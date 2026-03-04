import os
import time
import sqlite3
import discord
import shutil
from datetime import date, timedelta
from discord.ext import commands, tasks

# ===============================
# CREAR EL BOT AL INICIO
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
# MIGRACIÓN DE TABLA (AGREGA PRIMARY KEY SIN PERDER DATOS)
# ===============================
def asegurar_tabla_shulker_con_pk():
    """
    Si la tabla shulker existe sin PRIMARY KEY (user_id, fecha),
    la migra sin perder datos para que ON CONFLICT funcione.
    """
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

    # Revisar si tiene PK
    cursor.execute("PRAGMA table_info(shulker)")
    cols = cursor.fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    pk_cols = [c[1] for c in cols if c[5] > 0]

    # Queremos pk en (user_id, fecha)
    if set(pk_cols) == {"user_id", "fecha"}:
        print("✅ Tabla shulker ya tiene PRIMARY KEY (user_id, fecha)")
        return

    print("⚠️ Tabla shulker sin PRIMARY KEY correcto. Migrando sin perder datos...")

    # 1) renombrar tabla vieja
    cursor.execute("ALTER TABLE shulker RENAME TO shulker_old")

    # 2) crear tabla nueva con PK
    cursor.execute("""
    CREATE TABLE shulker (
        user_id INTEGER,
        username TEXT,
        fecha TEXT,
        total INTEGER,
        PRIMARY KEY (user_id, fecha)
    )
    """)

    # 3) copiar datos (si hay duplicados, los consolidamos sumando)
    cursor.execute("""
    INSERT INTO shulker (user_id, username, fecha, total)
    SELECT user_id, MAX(username) as username, fecha, SUM(total) as total
    FROM shulker_old
    GROUP BY user_id, fecha
    """)

    # 4) borrar tabla vieja
    cursor.execute("DROP TABLE shulker_old")
    db.commit()

    print("✅ Migración completada: shulker ahora tiene PRIMARY KEY y datos consolidados")

asegurar_tabla_shulker_con_pk()

# ===============================
# TABLA MENSAJES FIJOS
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
# CREAR EMBED RANKING
# ===============================
async def crear_embed_ranking(titulo, emoji, color, datos, footer):
    descripcion = "_Sin registros aún_" if not datos else ""
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
# ACTUALIZAR RANKINGS (EDITA, NO SPAM)
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
        await crear_embed_ranking("TOP MENSUAL", "👑", discord.Color.purple(), mensual, "🗓️ Mes actual"),
        await crear_embed_ranking("TOP SEMANAL", "📈", discord.Color.blue(), semanal, f"📅 Desde {inicio_semana}"),
        await crear_embed_ranking("TOP DIARIO", "⚡", discord.Color.gold(), diario, f"📆 Hoy • {hoy}")
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

        # ✅ UPSERT ATÓMICO (YA FUNCIONA PORQUE HAY PK)
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
    if os.path.exists(DB_PATH):
        size_kb = os.path.getsize(DB_PATH) / 1024
        print(f"   Tamaño actual: {size_kb:.2f} KB")

    if not ranking_automatico.is_running():
        ranking_automatico.start()

    # Mensaje fijo (no se repite)
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

# ===============================
# INICIAR BOT
# ===============================
bot.run(TOKEN)
