import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import os
import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional


def load_env_file(path: str = ".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key:
                os.environ.setdefault(key, value)


load_env_file()

DB_PATH = os.getenv("WORKOUTS_DB_PATH", "workouts.db")
token = os.getenv("DISCORD_BOT_TOKEN")
SYNC_COMMANDS = os.getenv("DISCORD_SYNC_COMMANDS", "false").strip().lower() in {"1", "true", "yes", "on"}
SYNC_GUILD_ID = os.getenv("DISCORD_SYNC_GUILD_ID")
LEADERBOARD_CHANNEL_ID = os.getenv("LEADERBOARD_CHANNEL_ID")
BOT_TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Europe/Zurich")

try:
    BOT_TIMEZONE = ZoneInfo(BOT_TIMEZONE_NAME)
except ZoneInfoNotFoundError as err:
    raise RuntimeError(f"Invalid BOT_TIMEZONE '{BOT_TIMEZONE_NAME}'") from err

try:
    LEADERBOARD_POST_HOUR = int(os.getenv("LEADERBOARD_POST_HOUR", "7"))
    LEADERBOARD_POST_MINUTE = int(os.getenv("LEADERBOARD_POST_MINUTE", "0"))
except ValueError as err:
    raise RuntimeError("LEADERBOARD_POST_HOUR and LEADERBOARD_POST_MINUTE must be integers") from err

if not (0 <= LEADERBOARD_POST_HOUR <= 23):
    raise RuntimeError("LEADERBOARD_POST_HOUR must be between 0 and 23")

if not (0 <= LEADERBOARD_POST_MINUTE <= 59):
    raise RuntimeError("LEADERBOARD_POST_MINUTE must be between 0 and 59")

LEADERBOARD_METRICS = {
    "calories_burned": "kcal",
    "distance_km": "km",
    "duration_min": "min",
}

if not token:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN. Add it to .env before starting the bot.")


# --- DATABASE SETUP ---
def setup_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                age INTEGER,
                weight_kg REAL,
                height_cm REAL,
                gender TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                activity TEXT,
                duration_min REAL,
                avg_hr INTEGER,
                distance_km REAL,
                calories_burned REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric TEXT,
                target REAL,
                start_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                end_timestamp DATETIME,
                is_active INTEGER DEFAULT 1
            )
        ''')
        conn.commit()


def save_profile(user_id: int, age: int, weight: float, height: float, gender: str):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (user_id, age, weight_kg, height_cm, gender)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            age=excluded.age, weight_kg=excluded.weight_kg, height_cm=excluded.height_cm, gender=excluded.gender
        ''', (user_id, age, weight, height, gender))
        conn.commit()


def fetch_user_profile(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT age, weight_kg, gender FROM users WHERE user_id = ?', (user_id,))
        return cursor.fetchone()


def insert_workout(user_id: int, activity: str, duration: float, avg_hr: int, calories: int, distance: Optional[float]):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO workouts (user_id, activity, duration_min, avg_hr, calories_burned, distance_km)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, activity, duration, avg_hr, calories, distance))
        conn.commit()


def fetch_history(user_id: int, limit: int, activity: Optional[str]):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        if activity:
            cursor.execute('''
                SELECT COUNT(*), SUM(calories_burned), SUM(distance_km)
                FROM workouts WHERE user_id = ? AND LOWER(activity) = LOWER(?)
            ''', (user_id, activity))
            totals = cursor.fetchone()

            cursor.execute('''
                SELECT id, activity, duration_min, avg_hr, calories_burned, timestamp, distance_km
                FROM workouts WHERE user_id = ? AND LOWER(activity) = LOWER(?)
                ORDER BY timestamp DESC LIMIT ?
            ''', (user_id, activity, limit))
        else:
            cursor.execute('''
                SELECT COUNT(*), SUM(calories_burned), SUM(distance_km)
                FROM workouts WHERE user_id = ?
            ''', (user_id,))
            totals = cursor.fetchone()

            cursor.execute('''
                SELECT id, activity, duration_min, avg_hr, calories_burned, timestamp, distance_km
                FROM workouts WHERE user_id = ?
                ORDER BY timestamp DESC LIMIT ?
            ''', (user_id, limit))

        recent_workouts = cursor.fetchall()

    return totals, recent_workouts


def fetch_last_workout(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, activity, duration_min, timestamp
            FROM workouts
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (user_id,))
        return cursor.fetchone()


def delete_workout(workout_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM workouts WHERE id = ?', (workout_id,))
        conn.commit()


def fetch_workout_by_id(user_id: int, workout_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, activity, duration_min, timestamp
            FROM workouts
            WHERE id = ? AND user_id = ?
            LIMIT 1
        ''', (workout_id, user_id))
        return cursor.fetchone()


def fetch_leaderboard_records(activity: Optional[str]):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        if activity:
            cursor.execute('''
                SELECT user_id, activity, duration_min, calories_burned, timestamp
                FROM workouts
                WHERE LOWER(activity) = LOWER(?)
                ORDER BY duration_min DESC
                LIMIT 1
            ''', (activity,))
            longest_wo = cursor.fetchone()

            cursor.execute('''
                SELECT user_id, activity, duration_min, calories_burned, timestamp
                FROM workouts
                WHERE LOWER(activity) = LOWER(?)
                ORDER BY calories_burned DESC
                LIMIT 1
            ''', (activity,))
            most_cals_wo = cursor.fetchone()

            cursor.execute('''
                SELECT user_id, activity, duration_min, distance_km, timestamp
                FROM workouts
                WHERE LOWER(activity) = LOWER(?) AND distance_km IS NOT NULL
                ORDER BY distance_km DESC
                LIMIT 1
            ''', (activity,))
            furthest_wo = cursor.fetchone()

            cursor.execute('''
                SELECT SUM(duration_min), SUM(calories_burned), SUM(distance_km)
                FROM workouts
                WHERE LOWER(activity) = LOWER(?)
            ''', (activity,))
        else:
            cursor.execute('''
                SELECT user_id, activity, duration_min, calories_burned, timestamp
                FROM workouts
                ORDER BY duration_min DESC
                LIMIT 1
            ''')
            longest_wo = cursor.fetchone()

            cursor.execute('''
                SELECT user_id, activity, duration_min, calories_burned, timestamp
                FROM workouts
                ORDER BY calories_burned DESC
                LIMIT 1
            ''')
            most_cals_wo = cursor.fetchone()

            cursor.execute('''
                SELECT user_id, activity, duration_min, distance_km, timestamp
                FROM workouts
                WHERE distance_km IS NOT NULL
                ORDER BY distance_km DESC
                LIMIT 1
            ''')
            furthest_wo = cursor.fetchone()

            cursor.execute('''
                SELECT SUM(duration_min), SUM(calories_burned), SUM(distance_km)
                FROM workouts
            ''')

        totals = cursor.fetchone()

    return longest_wo, most_cals_wo, furthest_wo, totals


def start_quest(metric: str, target: float, days: Optional[float]):
    if metric not in LEADERBOARD_METRICS:
        raise ValueError("Invalid quest metric")

    now = datetime.now(timezone.utc)
    start_str = now.strftime('%Y-%m-%d %H:%M:%S')

    end_str = None
    if days is not None:
        end_time = now + timedelta(days=days)
        end_str = end_time.strftime('%Y-%m-%d %H:%M:%S')

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE quests SET is_active = 0 WHERE is_active = 1')
        cursor.execute('''
            INSERT INTO quests (metric, target, start_timestamp, is_active, end_timestamp)
            VALUES (?, ?, ?, 1, ?)
        ''', (metric, target, start_str, end_str))
        conn.commit()


def fetch_active_quest():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT metric, target, start_timestamp, end_timestamp FROM quests WHERE is_active = 1 LIMIT 1')
        return cursor.fetchone()


def fetch_quest_progress(metric: str, start_timestamp: str, end_timestamp: Optional[str]):
    if metric not in LEADERBOARD_METRICS:
        raise ValueError("Invalid quest metric")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        if end_timestamp:
            cursor.execute(f'''
                SELECT SUM({metric})
                FROM workouts
                WHERE timestamp >= ? AND timestamp <= ?
            ''', (start_timestamp, end_timestamp))
        else:
            cursor.execute(f'''
                SELECT SUM({metric})
                FROM workouts
                WHERE timestamp >= ?
            ''', (start_timestamp,))
        return cursor.fetchone()[0] or 0


def fetch_leaderboard(metric: str, activity: Optional[str]):
    if metric not in LEADERBOARD_METRICS:
        raise ValueError("Invalid leaderboard metric")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        if activity:
            cursor.execute(f'''
                SELECT user_id, SUM({metric}) as total
                FROM workouts
                WHERE LOWER(activity) = LOWER(?)
                GROUP BY user_id
                ORDER BY total DESC
                LIMIT 10
            ''', (activity,))
        else:
            cursor.execute(f'''
                SELECT user_id, SUM({metric}) as total
                FROM workouts
                GROUP BY user_id
                ORDER BY total DESC
                LIMIT 10
            ''')

        return cursor.fetchall()


async def resolve_user_display(bot: commands.Bot, user_id: int, guild: Optional[discord.Guild] = None) -> str:
    if guild:
        member = guild.get_member(user_id)
        if member:
            return member.display_name

    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except discord.HTTPException:
            return f"User {user_id}"

    return user.display_name


async def build_leaderboard_description(
    bot: commands.Bot,
    rankings,
    db_metric: str,
    guild: Optional[discord.Guild] = None,
) -> str:
    rows = []
    rank_position = 0

    for uid, total in rankings:
        if total is None or total == 0:
            continue

        rank_position += 1
        medal = "🥇" if rank_position == 1 else "🥈" if rank_position == 2 else "🥉" if rank_position == 3 else f"**{rank_position}.**"
        user_label = await resolve_user_display(bot, uid, guild)
        val_str = f"{total:g} {LEADERBOARD_METRICS[db_metric]}"
        rows.append(f"{medal} {user_label} - **{val_str}**")

    return "\n\n".join(rows)

# --- BOT SETUP ---
class WorkoutBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        if SYNC_COMMANDS:
            if SYNC_GUILD_ID:
                try:
                    guild_id = int(SYNC_GUILD_ID)
                except ValueError as err:
                    raise RuntimeError("DISCORD_SYNC_GUILD_ID must be a valid integer guild id") from err

                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"Synced {len(synced)} commands to guild {guild_id}.", flush=True)
            else:
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} global commands.", flush=True)
        else:
            print("Skipping command sync. Set DISCORD_SYNC_COMMANDS=true to sync on startup.", flush=True)

        if LEADERBOARD_CHANNEL_ID:
            if not self.daily_leaderboard_post.is_running():
                self.daily_leaderboard_post.start()
            print(
                f"Daily leaderboard autopost enabled for {LEADERBOARD_POST_HOUR:02d}:{LEADERBOARD_POST_MINUTE:02d} ({BOT_TIMEZONE}).",
                flush=True,
            )
        else:
            print("Daily leaderboard autopost disabled. Set LEADERBOARD_CHANNEL_ID in .env to enable.", flush=True)

    @tasks.loop(time=time(hour=LEADERBOARD_POST_HOUR, minute=LEADERBOARD_POST_MINUTE, tzinfo=BOT_TIMEZONE))
    async def daily_leaderboard_post(self):
        if not LEADERBOARD_CHANNEL_ID:
            return

        try:
            channel_id = int(LEADERBOARD_CHANNEL_ID)
        except ValueError:
            print("Invalid LEADERBOARD_CHANNEL_ID. Autopost skipped.", flush=True)
            return

        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                print(f"Could not load channel {channel_id}. Autopost skipped.", flush=True)
                return

        if not isinstance(channel, discord.TextChannel):
            print(f"Channel {channel_id} is not a text channel. Autopost skipped.", flush=True)
            return

        rankings = await asyncio.to_thread(fetch_leaderboard, "calories_burned", None)
        if not rankings:
            print("No workout data available for daily leaderboard autopost.", flush=True)
            return

        description = await build_leaderboard_description(self, rankings, "calories_burned", channel.guild)
        if not description:
            print("No non-zero leaderboard rows to post.", flush=True)
            return

        embed = discord.Embed(
            title="🏆 Taegliche Rangliste (Kalorien)",
            description=description,
            color=discord.Color.gold(),
            timestamp=datetime.now(BOT_TIMEZONE),
        )
        embed.set_footer(text="Automatisch gepostet - ohne Ping")

        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        print(f"Posted daily leaderboard in channel {channel_id}.", flush=True)

    @daily_leaderboard_post.before_loop
    async def before_daily_leaderboard_post(self):
        await self.wait_until_ready()

setup_db()
bot = WorkoutBot()

# --- COMMANDS ---

@bot.tree.command(name="hilfe", description="Zeige eine Uebersicht aller Slash-Commands")
async def help_overview(interaction: discord.Interaction):
    slash_commands = sorted(
        (cmd for cmd in bot.tree.get_commands() if isinstance(cmd, app_commands.Command)),
        key=lambda cmd: cmd.name,
    )
    lines = [f"`/{cmd.name}` - {cmd.description or 'Keine Beschreibung'}" for cmd in slash_commands]

    embed = discord.Embed(
        title="Hilfe - Slash-Commands",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Tipp: Tippe / und waehle einen Command aus")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="profil", description="Initialisiere dein Fitnessprofil")
@app_commands.describe(
    alter="Alter in Jahren",
    gewicht="Gewicht in kg",
    groesse="Groesse in cm",
    geschlecht="Geschlecht"
)
@app_commands.choices(geschlecht=[
    app_commands.Choice(name="Maennlich", value="male"),
    app_commands.Choice(name="Weiblich", value="female")
])
async def init_profile(
    interaction: discord.Interaction,
    alter: app_commands.Range[int, 10, 120],
    gewicht: app_commands.Range[float, 30, 350],
    groesse: app_commands.Range[float, 100, 260],
    geschlecht: app_commands.Choice[str],
):
    user_id = interaction.user.id
    await asyncio.to_thread(save_profile, user_id, alter, gewicht, groesse, geschlecht.value)
    
    await interaction.response.send_message(
        f"Profil fuer **{interaction.user.display_name}** gespeichert: {alter} Jahre, {gewicht} kg, {groesse} cm, {geschlecht.name}."
    )

@bot.tree.command(name="eintrag", description="Trage ein Workout ein und berechne Kalorien")
@app_commands.describe(
    aktivitaet="Welche Aktivitaet? (z. B. Rudern, Laufen)",
    dauer="Dauer in Minuten",
    puls="Durchschnittlicher Puls (BPM)",
    distanz="Optional: Distanz in km"
)
async def log_workout(
    interaction: discord.Interaction,
    aktivitaet: str,
    dauer: app_commands.Range[float, 1, 720],
    puls: app_commands.Range[int, 30, 240],
    distanz: Optional[app_commands.Range[float, 0, 1000]] = None,
):
    user_id = interaction.user.id
    user_data = await asyncio.to_thread(fetch_user_profile, user_id)
    
    if not user_data:
        await interaction.response.send_message("❌ Fuehre zuerst `/profil` aus, damit ich deine Daten kenne!", ephemeral=True)
        return

    age, weight_kg, gender = user_data

    # Calorie Math
    if gender == 'male':
        calories = ((-55.0969 + (0.6309 * puls) + (0.1988 * weight_kg) + (0.2017 * age)) / 4.184) * dauer
    else:
        calories = ((-20.4022 + (0.4472 * puls) - (0.1263 * weight_kg) + (0.074 * age)) / 4.184) * dauer
    calories = max(0, round(calories))

    await asyncio.to_thread(insert_workout, user_id, aktivitaet, dauer, puls, calories, distanz)

    embed = discord.Embed(title="Workout gespeichert! 🏋️", color=discord.Color.green())
    embed.add_field(name="Aktivitaet", value=aktivitaet.capitalize(), inline=True)
    embed.add_field(name="Dauer", value=f"{dauer} min", inline=True)
    if distanz is not None:
        embed.add_field(name="Distanz", value=f"{distanz} km", inline=True)
    embed.add_field(name="Durchschnittspuls", value=f"{puls} bpm", inline=True)
    embed.add_field(name="🔥 Verbrannte Kalorien", value=f"**{calories} kcal**", inline=False)
    embed.set_footer(text=f"Starke Leistung, {interaction.user.display_name}!")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="undo", description="Entferne dein zuletzt eingetragenes Workout")
async def undo_workout(interaction: discord.Interaction):
    user_id = interaction.user.id
    last_workout = await asyncio.to_thread(fetch_last_workout, user_id)

    if not last_workout:
        await interaction.response.send_message("Du hast keine Workouts zum Entfernen.", ephemeral=True)
        return

    workout_id, activity, duration, timestamp = last_workout
    await asyncio.to_thread(delete_workout, workout_id)

    await interaction.response.send_message(
        f"Letztes Workout entfernt: **{activity.capitalize()}** ({duration:g} min) von {timestamp[:16]}.",
        ephemeral=True,
    )


@bot.tree.command(name="loeschen", description="Entferne einen bestimmten Workout-Eintrag per ID")
@app_commands.describe(workout_id="ID des Eintrags aus /verlauf")
async def delete_workout_entry(
    interaction: discord.Interaction,
    workout_id: int,
):
    user_id = interaction.user.id
    workout = await asyncio.to_thread(fetch_workout_by_id, user_id, workout_id)

    if not workout:
        await interaction.response.send_message(
            "Ich finde keinen Eintrag mit dieser ID in deinem Verlauf.",
            ephemeral=True,
        )
        return

    _, activity, duration, timestamp = workout
    await asyncio.to_thread(delete_workout, workout_id)

    await interaction.response.send_message(
        f"Eintrag geloescht: **{activity.capitalize()}** ({duration:g} min) von {timestamp[:16]} [ID {workout_id}].",
        ephemeral=True,
    )


@bot.tree.command(name="verlauf", description="Zeige letzte Workouts und Gesamtwerte")
@app_commands.describe(
    anzahl="Anzahl der letzten Workouts",
    aktivitaet="Nach Aktivitaet filtern"
)
async def workout_history(
    interaction: discord.Interaction,
    anzahl: app_commands.Range[int, 1, 25] = 5,
    aktivitaet: Optional[str] = None,
):
    user = interaction.user
    user_id = user.id
    
    totals, recent_workouts = await asyncio.to_thread(fetch_history, user_id, anzahl, aktivitaet)

    total_count = totals[0] or 0
    total_calories = totals[1] or 0
    total_distance = totals[2] or 0

    if total_count == 0:
        await interaction.response.send_message(f"{user.display_name} hat keine passenden Workouts.", ephemeral=True)
        return

    title = f"{user.display_name} - Verlauf: {aktivitaet.capitalize() if aktivitaet else 'Alle Aktivitaeten'}"
    embed = discord.Embed(title=title, color=discord.Color.blue())
    
    embed.add_field(name="Workouts gesamt", value=str(total_count), inline=True)
    embed.add_field(name="Kalorien gesamt", value=f"**{total_calories:g} kcal**", inline=True)
    if total_distance > 0:
        embed.add_field(name="Distanz gesamt", value=f"**{total_distance:g} km**", inline=True)
    
    embed.add_field(name="\u200b", value=f"**Letzte {min(anzahl, total_count)} Workouts:**", inline=False)
    
    for wo in recent_workouts:
        workout_id, act, duration, hr, cals, ts, dist = wo
        dist_str = f" | {dist:g} km" if dist is not None else ""
        embed.add_field(
            name=f"#{workout_id} - {act.capitalize()} am {ts[:16]}", 
            value=f"{duration:g} min{dist_str} | {hr} bpm | {cals:g} kcal", 
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rangliste", description="Zeige die Top-Rangliste fuer Workouts")
@app_commands.describe(
    kennzahl="Nach welcher Kennzahl sortieren?",
    aktivitaet="Optional: Nach Aktivitaet filtern (z. B. Laufen)"
)
@app_commands.choices(kennzahl=[
    app_commands.Choice(name="Verbrannte Kalorien", value="calories_burned"),
    app_commands.Choice(name="Distanz (km)", value="distance_km"),
    app_commands.Choice(name="Dauer (min)", value="duration_min")
])
async def leaderboard(
    interaction: discord.Interaction,
    kennzahl: app_commands.Choice[str],
    aktivitaet: Optional[str] = None,
):
    db_metric = kennzahl.value
    if db_metric not in LEADERBOARD_METRICS:
        await interaction.response.send_message("Ungueltige Kennzahl ausgewaehlt.", ephemeral=True)
        return

    rankings = await asyncio.to_thread(fetch_leaderboard, db_metric, aktivitaet)
    longest_wo, most_cals_wo, furthest_wo, server_totals = await asyncio.to_thread(fetch_leaderboard_records, aktivitaet)

    if not rankings:
        await interaction.response.send_message("Keine Daten fuer diese Rangliste gefunden!", ephemeral=True)
        return
        
    title_activity = aktivitaet.capitalize() if aktivitaet else "Alle Aktivitaeten"
    embed = discord.Embed(title=f"🏆 Rangliste: {kennzahl.name} ({title_activity})", color=discord.Color.gold())

    description = await build_leaderboard_description(bot, rankings, db_metric, interaction.guild)
    embed.description = description if description else "Keine Daten zum Anzeigen!"

    if longest_wo:
        uid, act, dur, cals, ts = longest_wo
        user_label = await resolve_user_display(bot, uid, interaction.guild)
        embed.add_field(
            name="Laengstes Einzel-Workout",
            value=f"{user_label}: **{act.capitalize()}** fuer **{dur:g} min** ({cals:g} kcal) am {ts[:16]}",
            inline=False,
        )

    if most_cals_wo:
        uid, act, dur, cals, ts = most_cals_wo
        user_label = await resolve_user_display(bot, uid, interaction.guild)
        embed.add_field(
            name="Meiste Kalorien in einem Workout",
            value=f"{user_label}: **{cals:g} kcal** bei **{act.capitalize()}** ({dur:g} min) am {ts[:16]}",
            inline=False,
        )

    if furthest_wo:
        uid, act, dur, dist, ts = furthest_wo
        if dist and dist > 0:
            user_label = await resolve_user_display(bot, uid, interaction.guild)
            embed.add_field(
                name="Weiteste Distanz in einem Workout",
                value=f"{user_label}: **{dist:g} km** bei **{act.capitalize()}** ({dur:g} min) am {ts[:16]}",
                inline=False,
            )

    total_duration = (server_totals[0] or 0) if server_totals else 0
    total_calories = (server_totals[1] or 0) if server_totals else 0
    total_distance = (server_totals[2] or 0) if server_totals else 0

    hours = int(total_duration // 60)
    minutes = total_duration % 60
    duration_str = f"{hours}h {minutes:g}m" if hours > 0 else f"{total_duration:g} min"

    embed.add_field(
        name="Server-Gesamtwerte",
        value=f"**{duration_str}** Training\n**{total_calories:g} kcal** verbrannt\n**{total_distance:g} km** zurueckgelegt",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="setquest", description="Starte eine serverweite Quest")
@app_commands.describe(
    kennzahl="Welche Kennzahl soll verfolgt werden?",
    ziel="Welcher Zielwert soll erreicht werden?",
    tage="Optional: Wie viele Tage soll die Quest laufen?",
)
@app_commands.choices(kennzahl=[
    app_commands.Choice(name="Verbrannte Kalorien", value="calories_burned"),
    app_commands.Choice(name="Distanz (km)", value="distance_km"),
    app_commands.Choice(name="Dauer (min)", value="duration_min"),
])
async def set_quest(
    interaction: discord.Interaction,
    kennzahl: app_commands.Choice[str],
    ziel: float,
    tage: Optional[app_commands.Range[float, 0.1, 365.0]] = None,
):
    if ziel <= 0:
        await interaction.response.send_message("Zielwert muss groesser als 0 sein.", ephemeral=True)
        return

    await asyncio.to_thread(start_quest, kennzahl.value, ziel, tage)

    unit = LEADERBOARD_METRICS[kennzahl.value]
    time_msg = f"⏳ Zeitlimit: **{tage:g} Tage**" if tage else "♾️ Kein Zeitlimit"

    await interaction.response.send_message(
        f"Neue Quest gestartet!\n"
        f"Ziel: **{ziel:g} {unit}** in **{kennzahl.name}**\n"
        f"{time_msg}\n"
        f"Nutze `/quest`, um den Fortschritt zu sehen.",
    )


@bot.tree.command(name="quest", description="Zeige den Fortschritt der aktuellen Quest")
async def check_quest(interaction: discord.Interaction):
    active_quest = await asyncio.to_thread(fetch_active_quest)

    if not active_quest:
        await interaction.response.send_message("Keine aktive Quest. Starte eine mit `/setquest`.", ephemeral=True)
        return

    metric, target, start_timestamp, end_timestamp = active_quest
    if metric not in LEADERBOARD_METRICS:
        await interaction.response.send_message("Die gespeicherte Quest ist ungueltig.", ephemeral=True)
        return

    now = datetime.now(timezone.utc)
    is_expired = False
    time_left_str = ""

    if end_timestamp:
        end_dt = datetime.strptime(end_timestamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        if now > end_dt:
            is_expired = True
        else:
            delta = end_dt - now
            days_left = delta.days
            hours_left = delta.seconds // 3600
            time_left_str = f"Restzeit: {days_left}d {hours_left}h"

    current_total = await asyncio.to_thread(fetch_quest_progress, metric, start_timestamp, end_timestamp)
    progress = min(current_total / target, 1.0)
    bar_len = 15
    filled = int(bar_len * progress)
    bar = "=" * filled + "-" * (bar_len - filled)

    unit = LEADERBOARD_METRICS[metric]
    metric_name = {
        "calories_burned": "Kalorien",
        "distance_km": "Distanz",
        "duration_min": "Dauer",
    }[metric]

    embed = discord.Embed(title="Aktive Community-Quest", color=discord.Color.blue())
    embed.description = f"**Ziel:** {target:g} {unit} ({metric_name})\n"

    if time_left_str:
        embed.description += f"{time_left_str}\n"

    if current_total >= target:
        embed.title = "Community-Quest (ERFUELLT)"
        embed.color = discord.Color.gold()
        embed.description += f"\nGeschafft!\n`[{('=' * bar_len)}]` **100%**"
    elif is_expired:
        embed.title = "Community-Quest (ABGELAUFEN)"
        embed.color = discord.Color.red()
        embed.description += (
            f"\nZeit abgelaufen.\n"
            f"Erreicht: {current_total:g} / {target:g} {unit}\n"
            f"`[{bar}]` **{progress * 100:.1f}%**"
        )
    else:
        embed.description += (
            f"\nFortschritt: {current_total:g} / {target:g} {unit}\n"
            f"`[{bar}]` **{progress * 100:.1f}%**"
        )

    embed.set_footer(text=f"Gestartet am {start_timestamp[:10]}")
    await interaction.response.send_message(embed=embed)
# Run the bot
bot.run(token)
