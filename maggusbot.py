import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from typing import Optional

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
    
    # We group by user_id, sum the chosen metric, and sort descending
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
    conn.close()

    if not rankings:
        await interaction.response.send_message("No data found for this leaderboard!", ephemeral=True)
        return
        
    title_activity = activity.capitalize() if activity else "All Activities"
    embed = discord.Embed(title=f"🏆 Leaderboard: {metric.name} ({title_activity})", color=discord.Color.gold())
    
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
        
        # Using <@user_id> automatically tags/shows their display name in Discord
        description += f"{medal} <@{uid}> - **{val_str}**\n\n"
        
    embed.description = description if description else "No data to show!"
    
    await interaction.response.send_message(embed=embed)
# Run the bot
bot.run(token)