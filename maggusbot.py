import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from typing import Optional
from datetime import datetime, timedelta, timezone

with open("bot_token.txt", "r") as f:
    token = f.readlines()[0]


# --- DATABASE SETUP ---
def setup_db():
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    # Table to store user info
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            age INTEGER,
            weight_kg REAL,
            height_cm REAL,
            gender TEXT
        )
    ''')

    # Table to store workouts
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

    # Quest
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
    conn.close()

# --- BOT SETUP ---
class WorkoutBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        # This syncs the slash commands to Discord when the bot starts
        await self.tree.sync()
        print("Bot is ready and slash commands are synced!")

setup_db()
bot = WorkoutBot()


# --- COMMANDS ---

# +++ init +++
# sets up the profile and saves important info about user, needed to accurately estimate the calories of a workout
@bot.tree.command(name="init", description="Initialize your fitness profile")
@app_commands.describe(
    age="Baujahr",
    weight="Wie fett bischt in kg?",
    height="Wie groß bischt in cm?",
    gender="Männlein oder Weiblein? 'Male' or 'Female'"
)
@app_commands.choices(gender=[
    app_commands.Choice(name="Male", value="male"),
    app_commands.Choice(name="Female", value="female")
])
async def init_profile(interaction: discord.Interaction, age: int, weight: float, height: float, gender: app_commands.Choice[str]):
    user_id = interaction.user.id
    
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    # Insert or update the user
    cursor.execute('''
        INSERT INTO users (user_id, age, weight_kg, height_cm, gender)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
        age=excluded.age, weight_kg=excluded.weight_kg, height_cm=excluded.height_cm, gender=excluded.gender
    ''', (user_id, age, weight, height, gender.value))
    
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"Profile saved for **{interaction.user.display_name}**: {age} yrs, {weight}kg, {height}cm, {gender.name}.")

# +++ logwo +++
# logs the workout by asking the user for a description of the activity, the duration, avg HR, and optionally the distance
@bot.tree.command(name="logwo", description="Log a workout and calculate calories burned")
@app_commands.describe(
    activity="What did you do? (e.g., Rowing, Running)",
    duration="Duration in minutes",
    avg_hr="Average Heart Rate (BPM)",
    distance="Optional: Distance in km"
)
async def log_workout(interaction: discord.Interaction, activity: str, duration: float, avg_hr: int, distance: Optional[float] = None):
    user_id = interaction.user.id
    
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT age, weight_kg, gender FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    
    if not user_data:
        await interaction.response.send_message("You need to run `/init` first so I know your stats!", ephemeral=True)
        conn.close()
        return

    age, weight_kg, gender = user_data

    # Calorie Math taken from Keytel at al 2005, https://doi.org/10.1080/02640410470001730089
    if gender == 'male':
        calories = ((-55.0969 + (0.6309 * avg_hr) + (0.1988 * weight_kg) + (0.2017 * age)) / 4.184) * duration
    else:
        calories = ((-20.4022 + (0.4472 * avg_hr) - (0.1263 * weight_kg) + (0.074 * age)) / 4.184) * duration
    calories = max(0, round(calories))

    # Save to database (now includes distance)
    cursor.execute('''
        INSERT INTO workouts (user_id, activity, duration_min, avg_hr, calories_burned, distance_km)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, activity, duration, avg_hr, calories, distance))
    
    conn.commit()
    conn.close()

    embed = discord.Embed(title="Workout Logged!", color=discord.Color.green())
    embed.add_field(name="Activity", value=activity.capitalize(), inline=True)
    embed.add_field(name="Duration", value=f"{duration} mins", inline=True)
    if distance:
        embed.add_field(name="Distance", value=f"{distance} km", inline=True)
    embed.add_field(name="Avg HR", value=f"{avg_hr} bpm", inline=True)
    embed.add_field(name="Calories Burned", value=f"**{calories} kcal**", inline=False)
    embed.set_footer(text=f"Great job, {interaction.user.display_name}!")

    await interaction.response.send_message(embed=embed)

# +++ undo +++
# undoes the last logged workout so the user can fix mistakes in the inputs

@bot.tree.command(name="undo", description="Remove your most recently logged workout")
async def undo_workout(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    # Fetch the most recent workout for this user
    cursor.execute('''
        SELECT id, activity, duration_min, timestamp 
        FROM workouts 
        WHERE user_id = ? 
        ORDER BY timestamp DESC 
        LIMIT 1
    ''', (user_id,))
    
    last_workout = cursor.fetchone()
    
    if not last_workout:
        await interaction.response.send_message("You don't have any logged workouts to remove.", ephemeral=True)
        conn.close()
        return
        
    workout_id, activity, duration, timestamp = last_workout
    
    # Delete the workout using its unique ID
    cursor.execute('DELETE FROM workouts WHERE id = ?', (workout_id,))
    conn.commit()
    conn.close()
    
    # Send confirmation
    await interaction.response.send_message(
        f"Successfully removed your last workout: **{activity.capitalize()}** ({duration:g} mins) from {timestamp[:16]}.", 
        ephemeral=True
    )


# +++ history +++
# shows the workout history of a user, limited to a specific number of workouts (defaults to 5) or type of acitivity

@bot.tree.command(name="history", description="Retrieve recent workouts and total stats")
@app_commands.describe(
    target_user="Check someone else's stats (leave blank for yours)",
    limit="Number of recent workouts to show",
    activity="Filter by a specific activity"
)
async def workout_history(interaction: discord.Interaction, target_user: Optional[discord.Member] = None, limit: Optional[int] = 5, activity: Optional[str] = None):
    # Determine whose stats we are looking at
    user = target_user or interaction.user
    user_id = user.id
    
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    if activity:
        cursor.execute('''
            SELECT COUNT(*), SUM(calories_burned), SUM(distance_km)
            FROM workouts WHERE user_id = ? AND LOWER(activity) = LOWER(?)
        ''', (user_id, activity))
        totals = cursor.fetchone()
        
        cursor.execute('''
            SELECT activity, duration_min, avg_hr, calories_burned, timestamp, distance_km
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
            SELECT activity, duration_min, avg_hr, calories_burned, timestamp, distance_km
            FROM workouts WHERE user_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (user_id, limit))

    recent_workouts = cursor.fetchall()
    conn.close()

    total_count = totals[0] or 0
    total_calories = totals[1] or 0
    total_distance = totals[2] or 0

    if total_count == 0:
        await interaction.response.send_message(f"{user.display_name} has no workouts matching your criteria.", ephemeral=True)
        return

    title = f"{user.display_name}'s History: {activity.capitalize() if activity else 'All Activities'}"
    embed = discord.Embed(title=title, color=discord.Color.blue())
    
    embed.add_field(name="Total Workouts", value=str(total_count), inline=True)
    embed.add_field(name="Total Calories", value=f"**{total_calories:g} kcal**", inline=True)
    if total_distance > 0:
        embed.add_field(name="Total Distance", value=f"**{total_distance:g} km**", inline=True)
    
    embed.add_field(name="\u200b", value=f"**Last {min(limit, total_count)} Workouts:**", inline=False)
    
    for wo in recent_workouts:
        act, duration, hr, cals, ts, dist = wo
        dist_str = f" | {dist:g} km" if dist else ""
        embed.add_field(
            name=f"{act.capitalize()} on {ts[:16]}", 
            value=f"{duration:g} mins{dist_str} | {hr} bpm | {cals:g} kcal", 
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# +++ leaderboard +++
# shows the leaderboard over all users and logged workouts, based on a chosen metric or optionally by a specific activity

@bot.tree.command(name="leaderboard", description="View the top ranking for workouts")
@app_commands.describe(
    metric="What metric to rank by",
    activity="Optional: Filter by a specific activity (e.g., Running)"
)
@app_commands.choices(metric=[
    app_commands.Choice(name="Calories Burned", value="calories_burned"),
    app_commands.Choice(name="Distance (km)", value="distance_km"),
    app_commands.Choice(name="Duration (mins)", value="duration_min")
])
async def leaderboard(interaction: discord.Interaction, metric: app_commands.Choice[str], activity: Optional[str] = None):
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    db_metric = metric.value 
    
    # --- 1. Fetch Leaderboard Rankings ---
    if activity:
        cursor.execute(f'''
            SELECT user_id, SUM({db_metric}) as total 
            FROM workouts 
            WHERE LOWER(activity) = LOWER(?)
            GROUP BY user_id 
            ORDER BY total DESC 
            LIMIT 10
        ''', (activity,))
    else:
        cursor.execute(f'''
            SELECT user_id, SUM({db_metric}) as total 
            FROM workouts 
            GROUP BY user_id 
            ORDER BY total DESC 
            LIMIT 10
        ''')
        
    rankings = cursor.fetchall()

    # --- 2. Fetch Single Record Workouts ---
    if activity:
        # Longest single workout for this activity
        cursor.execute('''
            SELECT user_id, activity, duration_min, calories_burned, timestamp 
            FROM workouts 
            WHERE LOWER(activity) = LOWER(?) 
            ORDER BY duration_min DESC LIMIT 1
        ''', (activity,))
        longest_wo = cursor.fetchone()

        # Highest calories burned in a single workout for this activity
        cursor.execute('''
            SELECT user_id, activity, duration_min, calories_burned, timestamp 
            FROM workouts 
            WHERE LOWER(activity) = LOWER(?) 
            ORDER BY calories_burned DESC LIMIT 1
        ''', (activity,))
        most_cals_wo = cursor.fetchone()

        # Longest distance covered in a single workout for this activity
        cursor.execute('''
            SELECT user_id, activity, duration_min, distance_km, timestamp 
            FROM workouts 
            WHERE LOWER(activity) = LOWER(?) AND distance_km IS NOT NULL
            ORDER BY distance_km DESC LIMIT 1
        ''', (activity,))
        furthest_wo = cursor.fetchone()
    else:
        # Longest single workout overall
        cursor.execute('''
            SELECT user_id, activity, duration_min, calories_burned, timestamp 
            FROM workouts 
            ORDER BY duration_min DESC LIMIT 1
        ''')
        longest_wo = cursor.fetchone()

        # Highest calories burned in a single workout overall
        cursor.execute('''
            SELECT user_id, activity, duration_min, calories_burned, timestamp 
            FROM workouts 
            ORDER BY calories_burned DESC LIMIT 1
        ''')
        most_cals_wo = cursor.fetchone()

        # Longest distance covered in a single workout overall
        cursor.execute('''
            SELECT user_id, activity, duration_min, distance_km, timestamp 
            FROM workouts 
            WHERE distance_km IS NOT NULL
            ORDER BY distance_km DESC LIMIT 1
        ''')
        furthest_wo = cursor.fetchone()


    # --- 3. Fetch Server Totals ---
    if activity:
        cursor.execute('''
            SELECT SUM(duration_min), SUM(calories_burned), SUM(distance_km)
            FROM workouts
            WHERE LOWER(activity) = LOWER(?)
        ''', (activity,))
    else:
        cursor.execute('''
            SELECT SUM(duration_min), SUM(calories_burned), SUM(distance_km)
            FROM workouts
        ''')
    
    server_totals = cursor.fetchone()
    total_duration = server_totals[0] or 0
    total_calories = server_totals[1] or 0
    total_distance = server_totals[2] or 0

    conn.close()

    # --- 4. Build the Response Embed ---
    if not rankings:
        await interaction.response.send_message("No data found for this leaderboard!", ephemeral=True)
        return
        
    title_activity = activity.capitalize() if activity else "All Activities"
    embed = discord.Embed(title=f"🏆 Leaderboard: {metric.name} ({title_activity})", color=discord.Color.gold())
    
    # Build Top 10 Description
    description = ""
    for i, (uid, total) in enumerate(rankings, 1):
        if total is None or total == 0: 
            continue # Skip users who have 0 for this specific metric
        
        # Format the unit based on the chosen metric
        if db_metric == "distance_km":
            val_str = f"{total:g} km"
        elif db_metric == "duration_min":
            val_str = f"{total:g} mins"
        else:
            val_str = f"{total:g} kcal"
            
        # Add medals for top 3
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        description += f"{medal} <@{uid}> - **{val_str}**\n\n"
        
    embed.description = description if description else "No data to show!"
    
    # --- 5. Add Record Workout Fields ---
    if longest_wo:
        uid, act, dur, cals, ts = longest_wo
        embed.add_field(
            name="Longest Single Workout", 
            value=f"<@{uid}> did **{act.capitalize()}** for **{dur:g} mins** ({cals:g} kcal) on {ts[:16]}", 
            inline=False
        )
        
    if most_cals_wo:
        uid, act, dur, cals, ts = most_cals_wo
        embed.add_field(
            name="Most Calories Burned in One Workout", 
            value=f"<@{uid}> burned **{cals:g} kcal** doing **{act.capitalize()}** ({dur:g} mins) on {ts[:16]}", 
            inline=False
        )

    if furthest_wo:
        uid, act, dur, dist, ts = furthest_wo
        if dist and dist > 0: # Ensure distance is actually greater than 0
            embed.add_field(
                name="Longest Distance Covered in One Workout", 
                value=f"<@{uid}> covered **{dist:g} km** doing **{act.capitalize()}** ({dur:g} mins) on {ts[:16]}", 
                inline=False
            )
    

    # --- 6. Add Server Grand Totals Field ---
    # Convert huge minute numbers into hours/minutes for readability!
    hours = total_duration // 60
    mins = total_duration % 60
    dur_str = f"{hours:g}h {mins:g}m" if hours > 0 else f"{total_duration:g} mins"

    embed.add_field(
        name="🌍 Server Grand Totals", 
        value=f"**{dur_str}** spent exercising\n**{total_calories:g} kcal** burned\n**{total_distance:g} km** traveled", 
        inline=False
    )



# setquest
# set server quest, e.g. calorie goal for a week
@bot.tree.command(name="setquest", description="Start a new server-wide fitness quest!")
@app_commands.describe(
    metric="What are we tracking?",
    target="The goal number to reach",
    days="Optional: How many days until the quest expires? (e.g. 7 for a week)"
)
@app_commands.choices(metric=[
    app_commands.Choice(name="Calories Burned", value="calories_burned"),
    app_commands.Choice(name="Distance (km)", value="distance_km"),
    app_commands.Choice(name="Duration (mins)", value="duration_min")
])
async def set_quest(interaction: discord.Interaction, metric: app_commands.Choice[str], target: float, days: Optional[float] = None):
    if target <= 0:
        await interaction.response.send_message("Target must be greater than 0", ephemeral=True)
        return

    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    # End any currently active quests
    cursor.execute('UPDATE quests SET is_active = 0 WHERE is_active = 1')
    
    # Calculate timestamps (SQLite uses UTC by default)
    now = datetime.now(timezone.utc)
    start_str = now.strftime('%Y-%m-%d %H:%M:%S')
    
    end_str = None
    if days:
        end_time = now + timedelta(days=days)
        end_str = end_time.strftime('%Y-%m-%d %H:%M:%S')
    
    # Start the new quest
    cursor.execute('''
        INSERT INTO quests (metric, target, start_timestamp, is_active, end_timestamp)
        VALUES (?, ?, ?, 1, ?)
    ''', (metric.value, target, start_str, end_str))
    
    conn.commit()
    conn.close()
    
    unit = "km" if metric.value == "distance_km" else "mins" if metric.value == "duration_min" else "kcal"
    
    time_msg = f"⏳ You have **{days:g} days** to complete it!" if days else "♾️ This quest has no time limit!"
    
    await interaction.response.send_message(
        f"! **NEW SERVER QUEST STARTED** !\n"
        f"Our community goal is **{target:g} {unit}** of {metric.name}.\n"
        f"{time_msg}\n"
        f"Log your workouts to contribute. Run `/quest` to check the progress!"
    )



# quest
# fetch current quest status
@bot.tree.command(name="quest", description="Check the progress of the current server quest")
async def check_quest(interaction: discord.Interaction):
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    
    # Fetch the active quest
    cursor.execute('SELECT metric, target, start_timestamp, end_timestamp FROM quests WHERE is_active = 1 LIMIT 1')
    quest = cursor.fetchone()
    
    if not quest:
        await interaction.response.send_message("❌ There is no active quest right now. Use `/setquest` to start one!", ephemeral=True)
        conn.close()
        return
        
    metric, target, start_timestamp, end_timestamp = quest
    
    # --- Time & Expiration Math ---
    now = datetime.now(timezone.utc)
    is_expired = False
    time_left_str = ""
    
    if end_timestamp:
        # Parse the expiration date from the database
        end_dt = datetime.strptime(end_timestamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        
        if now > end_dt:
            is_expired = True
        else:
            delta = end_dt - now
            days_left = delta.days
            hours_left = delta.seconds // 3600
            time_left_str = f"⏳ **Time Remaining:** {days_left}d {hours_left}h\n"
    
    # --- Calculate Progress ---
    # If the quest has a time limit, only count workouts logged BEFORE the time limit expired
    if end_timestamp:
        cursor.execute(f'''
            SELECT SUM({metric}) FROM workouts 
            WHERE timestamp >= ? AND timestamp <= ?
        ''', (start_timestamp, end_timestamp))
    else:
        cursor.execute(f'''
            SELECT SUM({metric}) FROM workouts 
            WHERE timestamp >= ?
        ''', (start_timestamp,))
        
    current_total = cursor.fetchone()[0] or 0
    conn.close()
    
    # Formatting text based on what we are tracking
    unit = "km" if metric == "distance_km" else "mins" if metric == "duration_min" else "kcal"
    metric_name = "Distance" if metric == "distance_km" else "Duration" if metric == "duration_min" else "Calories Burned"
    
    # --- Progress Bar Math ---
    percent = min(current_total / target, 1.0)
    length = 15 # How many blocks long the bar is
    filled = int(length * percent)
    bar = '▓' * filled + '░' * (length - filled)
    
    # --- Build the Embed ---
    embed = discord.Embed(title="Active Community Quest", color=discord.Color.blue())
    embed.description = f"**Goal:** Reach **{target:g} {unit}** ({metric_name})\n"
    embed.description += time_left_str + "\n"
    
    if current_total >= target:
        embed.title = "Community Quest (COMPLETED)"
        embed.description += f" **QUEST COMPLETE!** \nWe crushed the goal of {target:g} {unit}!\n\n`[{'▓'*length}]` **100%**!"
        embed.color = discord.Color.gold()
        
    elif is_expired:
        embed.title = "Community Quest (FAILED)"
        embed.description += f" **TIME IS UP!** \nWe reached {current_total:g} {unit}, but fell short of the {target:g} {unit} goal.\n\n`[{bar}]` **{percent*100:.1f}%**"
        embed.color = discord.Color.red()
        
    else:
        embed.description += f"**Progress:** {current_total:g} / {target:g} {unit}\n"
        embed.description += f"`[{bar}]` **{percent*100:.1f}%**\n\n"
        embed.description += "Keep logging those workouts to help the server reach the goal!"
        
    embed.set_footer(text=f"Quest started on {start_timestamp[:10]}")
    
    await interaction.response.send_message(embed=embed)

# Run the bot
bot.run(token)