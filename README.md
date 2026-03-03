# Maggusbot (Discord Workout Tracker)

Maggusbot is a Discord bot built with Python and `discord.py` for tracking workouts on a server.
It stores data in SQLite, supports German slash commands, and includes leaderboard + quest features.

## Features

- Workout logging with calorie estimation (Keytel et al. formula)
- User fitness profiles (age, weight, height, gender)
- History and activity filters per user
- Leaderboards by calories, distance, or duration
- Daily automatic leaderboard post to a configured channel
- Undo of the most recently logged workout
- Server quests (`/setquest`, `/quest`) with optional time limit
- Environment-based configuration (`.env`) for token, sync behavior, DB path, and scheduling

## Slash Commands (German)

| Command | Description |
| :--- | :--- |
| `/hilfe` | Shows an overview of all available slash commands. |
| `/profil` | Creates/updates your fitness profile. Required before workout logging. |
| `/eintrag` | Logs a workout and calculates calories. |
| `/undo` | Removes your most recent workout entry. |
| `/verlauf` | Shows workout history and totals (optionally filtered). |
| `/rangliste` | Shows leaderboard for calories, distance, or duration. |
| `/setquest` | Starts a server-wide quest with goal and optional duration. |
| `/quest` | Shows current quest progress and status. |

## Requirements

- Python 3.9+
- A Discord bot application/token
- `discord.py`

Install dependency:

```bash
pip install discord.py
```

## Configuration (.env)

Create a `.env` file in the project root.

```env
DISCORD_BOT_TOKEN=your_bot_token_here

# Optional
WORKOUTS_DB_PATH=workouts.db
DISCORD_SYNC_COMMANDS=true
DISCORD_SYNC_GUILD_ID=123456789012345678
LEADERBOARD_CHANNEL_ID=123456789012345678
BOT_TIMEZONE=Europe/Zurich
LEADERBOARD_POST_HOUR=7
LEADERBOARD_POST_MINUTE=0
```

### Environment Variables

| Variable | Required | Default | Purpose |
| :--- | :---: | :--- | :--- |
| `DISCORD_BOT_TOKEN` | yes | - | Bot token used by `bot.run(...)`. |
| `WORKOUTS_DB_PATH` | no | `workouts.db` | SQLite database path. |
| `DISCORD_SYNC_COMMANDS` | no | `false` | Enable slash-command sync on startup. |
| `DISCORD_SYNC_GUILD_ID` | no | unset | If set, sync commands to one guild for faster updates. |
| `LEADERBOARD_CHANNEL_ID` | no | unset | Enables daily leaderboard autopost in this channel. |
| `BOT_TIMEZONE` | no | `Europe/Zurich` | Timezone for scheduled daily leaderboard post. |
| `LEADERBOARD_POST_HOUR` | no | `7` | Posting hour (0-23). |
| `LEADERBOARD_POST_MINUTE` | no | `0` | Posting minute (0-59). |

## Run

```bash
python maggusbot.py
```

If command syncing is enabled, startup output will show sync status.
If leaderboard autopost is enabled, startup output will show schedule + timezone.

## Bot Invite

In Discord Developer Portal -> OAuth2 -> URL Generator:

- Scopes: `bot`, `applications.commands`
- Typical permissions: Send Messages, Embed Links, Read Message History

Then open the generated URL and invite the bot to your server.

## Notes

- Data is stored locally in SQLite.
- `quests` table is created automatically on startup.
- The bot reads `.env` automatically; no hardcoded token file is required.

## Calorie Formula

Based on Keytel et al. (2005):

- Male: `((-55.0969 + 0.6309*HR + 0.1988*Weight + 0.2017*Age) / 4.184) * Duration`
- Female: `((-20.4022 + 0.4472*HR - 0.1263*Weight + 0.074*Age) / 4.184) * Duration`
