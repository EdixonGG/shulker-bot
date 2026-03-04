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
# RUTA DE LA BASE DE DATOS (PERSISTENTE EN RAILWAY)
# ===============================
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "shulker.db")
OLD_DB_PATH = "shulker.db"  # el que está en la raíz (si existe)

# ===============================
# MIGRACIÓN AUTOMÁTICA (solo si existe DB vieja y aún no hay DB en /data)
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
# BASE DE DATOS
# ===============================
db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode=WAL;")         # mejor concurrencia
db.execute("PRAGMA synchronous=NORMAL;")
cursor = db.cursor()

# Tabla principal: ahora con PRIMARY KEY (user_id, fecha)
cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker (
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, fecha)
)
""")

# Tabla para guardar IDs de mensajes fijos (botón, etc.)
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
    """
    Devuelve el mensaje fijo asociado a 'tipo'.
    Si no existe o fue borrado, crea uno nuevo, guarda el ID y lo devuelve.
    """
    cursor.execute("SELECT message_id FROM mensajes_fijos WHERE tipo = ?", (tipo,))
    row = cursor.fetchone()

    if row:
        msg_id = row[0]
        try:
            return await channel.fetch_message(msg_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # si fue borrado o no se puede acceder, se recrea
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

    # Tomamos hasta 6 por si se queda un mensaje “viejo” extra
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

        # Cooldown anti-spam
        if user_id in cooldowns and ahora - cooldowns[user_id] < COOLDOWN_SECONDS:
            await interaction.response.send_message("⏳ Espera antes de registrar.", ephemeral=True)
            return
        cooldowns[user_id] = ahora

        # Validación
        try:
            cantidad_int = int(self.cantidad.value)
            if cantidad_int <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Número inválido.", ephemeral=True)
            return

        hoy = str(date.today())
        username = interaction.user.display_name

        # ✅ UPSERT ATÓMICO (ANTI-DUPLICADO)
        # Inserta si no existe (user_id, fecha) o suma si ya existe.
        cursor.execute("""
            INSERT INTO shulker (user_id, username, fecha, total)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, fecha)
            DO UPDATE SET
                total = total + excluded.total,
                username = excluded.username
        """, (user_id, username, hoy, cantidad_int))
        db.commit()

        # Leer total actualizado para el embed de log
        cursor.execute("SELECT total FROM shulker WHERE user_id = ? AND fecha = ?", (user_id, hoy))
        nuevo_total = cursor.fetchone()[0]

        await actualizar_todos_los_ranking()

        # Log al canal END
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

    # Mensaje fijo del botón (no se repite al reiniciar)
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
