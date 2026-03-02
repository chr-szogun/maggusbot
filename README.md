# Discord Workout Tracker Bot

A lightweight Discord bot built with Python and `discord.py` that allows server members to log their workouts, estimate calories burned, track their history, and compete on server-wide leaderboards. 
Data is stored locally using an SQLite database. Any and all similarities to german bodybuilding legend Markus Rühl is purely coincedental.

##Features
* **Accurate Calorie Tracking**: Uses the scientifically validated Keytel et al. formula to calculate energy expenditure based on Heart Rate, Age, Weight, and Gender.
* **Modern Slash Commands**: Fully integrates with Discord's native slash command UI for easy input.
* **Server Leaderboards**: Compete with friends by ranking top calories burned, distance covered, or duration exercised.
* **Filterable History**: View your own or others' past workouts, filtered by specific activities.
* **Local Storage**: Uses a lightweight `workouts.db` SQLite file. No database servers required!

## Available Commands

| Command | Description |
| :--- | :--- |
| `/init` | Initialize your fitness profile (Age, Weight, Height, Gender). **Required before logging!** |
| `/logwo` | Log a workout. Requires Activity, Duration, and Avg Heart Rate. Optionally takes Distance (km). |
| `/history` | View recent workouts and totals. Can be filtered by activity, and you can check other users' stats. |
| `/leaderboard` | View the top 10 rankings for Calories, Distance, or Duration. Can be filtered by specific activities. |
| `/undo` | Made a mistake? Quickly delete your most recently logged workout. |

## Prerequisites
1. **Python 3.8+**
2. A Discord Bot Token (Get one from the [Discord Developer Portal](https://discord.com/developers/applications)).
3. The `discord.py` library.

## Installation & Setup

**1. Clone or download this repository**
Ensure `bot.py` is in your working directory.

**2. Install dependencies**
Open your terminal or command prompt and run:
```bash
pip install discord.py
```

**3. Configure the Bot**
Open `bot.py` in your code editor and update the following placeholders:
* Find `self.MY_GUILD = discord.Object(id=YOUR_SERVER_ID)` and replace `YOUR_SERVER_ID` with your actual Discord Server ID. *(To get this: User Settings > Advanced > Turn on Developer Mode, then right-click your server icon and click "Copy Server ID".)*
* Find `bot.run('YOUR_BOT_TOKEN_HERE')` at the very bottom and paste your secret Bot Token.

**4. Invite the Bot to your Server**
In the Discord Developer portal, go to **OAuth2 > URL Generator**. 
Select the `bot` and `applications.commands` scopes. Give it basic text permissions (Send Messages, Embed Links). Copy the generated URL, paste it into your browser, and invite the bot to your server.

**5. Run the Bot**
```bash
python bot.py
```
You should see a message in your console saying the bot is ready and commands are synced!

## Troubleshooting

**I don't see the Slash Commands in my server!**
Discord caches slash commands. If you just started the bot and don't see the commands:
* **If on desktop:** Press `Ctrl + R` (or `Cmd + R`) to force refresh Discord.
* **If on browser:** Press `F5` to refresh the webpage. 
* Ensure you properly replaced `YOUR_SERVER_ID` in the code, which tells Discord to sync the commands to your testing server instantly.

## How Calories are Calculated
This bot uses the **Keytel et al. (2005)** formula for estimating Energy Expenditure (EE). It is widely considered one of the most accurate formulas for heart-rate-based calorie estimation without using a VO2 max mask.

* **Male:** `EE = (-55.0969 + (0.6309 × HR) + (0.1988 × Weight) + (0.2017 × Age)) / 4.184 * duration`
* **Female:** `EE = (-20.4022 + (0.4472 × HR) - (0.1263 × Weight) + (0.074 × Age)) / 4.184 * duration`
