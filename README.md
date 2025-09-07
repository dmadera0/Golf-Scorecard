# ⛳ Golf Scorecard CLI

A command-line application for creating golf scorecards, recording strokes, and interactively tracking a round of golf.  
Built with **Python 3**, **Postgres**, and **prompt_toolkit**.

---

## Features

- **Create Scorecard**
  - Enter course name, date, and 1–4 players.
  - Unique `game_id` automatically generated (e.g. `Angel Park - 09/01/2025`).

- **Record Score**
  - Menu-driven input to record strokes for any player/hole.

- **Show Scorecard**
  - Classic 18×4 scorecard grid.
  - Includes **Front 9**, **Back 9**, and **Total** summaries.

- **Interactive Scorecard (TUI)**
  - Navigate with arrow keys.
  - Edit strokes with **1–8** keys.
  - Edit par values with **3–6** keys in the Par column.
  - Live updating **Front/Back/Total** for par and players.
  - Clean highlighting (no raw ANSI codes) using prompt_toolkit styles.

- **Totals**
  - View each player’s number of holes completed and running total.

- **All Scorecards**
  - List every stored scorecard.

---

## Installation

### Requirements
- Python 3.9+
- PostgreSQL 13+ (with `uuid-ossp` extension available)

### Python dependencies
```bash
pip install "psycopg[binary]" prompt_toolkit python-dotenv
Database Setup
Create a database:

bash
Copy code
createdb -U <youruser> golf
(Optional) Apply schema manually:

bash
Copy code
psql -U <youruser> -d golf -f schema.sql
Or let the app create all tables automatically on first run.

Make sure the uuid-ossp extension is enabled (done automatically by the app if you’re a superuser).

Configuration
The app looks for the DB_CONN environment variable. Example:

bash
Copy code
export DB_CONN="dbname=golf user=$(whoami) host=localhost"
Alternatively, place it in a .env file in the repo root:

ini
Copy code
DB_CONN=dbname=golf user=d.madera host=localhost
Usage
Run the CLI:

bash
Copy code
python3 golf_cli.py
Menu:

markdown
Copy code
--- Golf CLI ---
1. Create Scorecard
2. Record Score
3. Show Scorecard
4. Interactive Scorecard
5. Total Score
6. All Scorecards
7. Exit
Interactive Controls
Arrow Keys: Move between cells.

Par column (col 0): Set par with keys 3–6.

Player cells: Set strokes with keys 1–8.

q: Quit interactive mode.

Schema
Tables created:

games: stores each scorecard header.

players: players for a given game.

scores: strokes per hole per player.

hole_pars: par values per hole per game.

Roadmap / Ideas
Export scorecards to CSV/PDF.

Track handicap, net scores, skins.

Multiplayer live updates (network sync).

Configurable max strokes.

Store course data locally.

