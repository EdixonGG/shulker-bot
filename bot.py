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

TOKEN = os.getenv("DISCORD_TOKEN")
COOLDOWN_SECONDS = 60

# ===============================
# INTENTS
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ===============================
# BASE DE DATOS
# ===============================
db = sqlite3.connect("shulker.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shulker (
    user_id INTEGER,
    username TEXT,
    fecha TEXT,
    total INTEGER,
    PRIMARY KEY (user_id, fecha)
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

def ultimo_mes_cerrado():
    hoy = date.today()
    primero_mes_actual = hoy.replace(day=1)
    ultimo_dia_mes_anterior = primero_mes_actual - timedelta(days=1)
    return ultimo_dia_mes_anterior.strftime("%Y-%m")

# ===============================
# EMBED HISTORIAL MENSUAL
# ===============================
async def crear_embed_historial(mes, ranking, total):
    stacks = total * 27
    bloques = stacks * 64
    niveles = bloques * 9

    desc = ""
    for i, (user, t) in enumerate(ranking, 1):
        medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
        desc += f"{medalla} **{user}** — `{format_number(t)}` shulker\n"

    embed = discord.Embed(
        title=f"🏆 Historial Mensual • {mes}",
        description=desc or "_Sin datos_",
        color=discord.Color.dark_gold()
    )

    embed.add_field(name="📦 Total Shulkers", value=f"`{format_number(total)}`", inline=True)
    embed.add_field(name="🧱 Bloques End", value=f"`{format_number(bloques)}`", inline=True)
    embed.add_field(name="📈 Niveles Isla", value=f"`{format_number(niveles)}`", inline=True)

    embed.set_footer(text="Mes cerrado automáticamente")
    return embed

# ===============================
# 🔒 CIERRE MENSUAL AUTOMÁTICO
# ===============================
async def cerrar_mes_anterior():
    mes_cerrar = ultimo_mes_cerrado()

    cursor.execute("SELECT 1 FROM historial_mensual WHERE mes = ?", (mes_cerrar,))
    if cursor.fetchone():
        return

    inicio = date.fromisoformat(mes_cerrar + "-01")
    fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    cursor.execute("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha BETWEEN ? AND ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, (str(inicio), str(fin)))

    ranking = cursor.fetchall()
    total = sum(r[1] for r in ranking)

    if total == 0:
        return

    cursor.execute("""
        INSERT INTO historial_mensual (mes, ranking, total_shulker, creado_en)
        VALUES (?, ?, ?, ?)
    """, (mes_cerrar, json.dumps(ranking), total, str(date.today())))

    db.commit()
    print(f"✅ Mes cerrado correctamente: {mes_cerrar}")

# ===============================
# ACTUALIZAR RANKINGS
# ===============================
async def actualizar_todos_los_ranking():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel:
        return

    await cerrar_mes_anterior()

    hoy = date.today()
    inicio_mes = hoy.replace(day=1)
    inicio_semana = hoy - timedelta(days=hoy.weekday())

    def ranking(query, param):
        cursor.execute(query, (param,))
        return cursor.fetchall()

    mensual = ranking("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, str(inicio_mes))

    semanal = ranking("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha >= ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, str(inicio_semana))

    diario = ranking("""
        SELECT username, SUM(total)
        FROM shulker
        WHERE fecha = ?
        GROUP BY user_id
        ORDER BY SUM(total) DESC
    """, str(hoy))

    cursor.execute("""
        SELECT mes, ranking, total_shulker
        FROM historial_mensual
        WHERE mes = ?
    """, (ultimo_mes_cerrado(),))

    hist = cursor.fetchone()

    embeds = []

    if hist:
        embeds.append(await crear_embed_historial(
            hist[0],
            json.loads(hist[1]),
            hist[2]
        ))

    def embed_top(titulo, emoji, color, datos):
        d = ""
        for i, (u, t) in enumerate(datos, 1):
            medalla = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            d += f"{medalla} **{u}** — `{format_number(t)}`\n"
        return discord.Embed(title=f"{emoji} {titulo}", description=d or "_Sin registros_", color=color)

    embeds += [
        embed_top("TOP MENSUAL ACTUAL", "👑", discord.Color.purple(), mensual),
        embed_top("TOP SEMANAL", "📈", discord.Color.blue(), semanal),
        embed_top("TOP DIARIO", "⚡", discord.Color.gold(), diario)
    ]

    mensajes = []
    async for msg in channel.history(limit=20):
        if msg.author == bot.user and msg.embeds:
            mensajes.append(msg)

    for i, e in enumerate(embeds):
        if i < len(mensajes):
            await mensajes[i].edit(embed=e)
        else:
            await channel.send(embed=e)

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
            restante = int(COOLDOWN_SECONDS - (ahora - cooldowns[user_id]))
            return await interaction.response.send_message(
                f"⏳ Espera {restante}s antes de registrar.",
                ephemeral=True
            )

        try:
            cantidad = int(self.cantidad.value)
            if cantidad <= 0:
                raise ValueError
        except:
            return await interaction.response.send_message("❌ Número inválido.", ephemeral=True)

        cooldowns[user_id] = ahora

        hoy = str(date.today())
        username = interaction.user.display_name

        cursor.execute("""
            INSERT INTO shulker VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, fecha)
            DO UPDATE SET total = total + ?
        """, (user_id, username, hoy, cantidad, cantidad))
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
