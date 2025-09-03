#!/usr/bin/env python3
"""
Golf Scorecard CLI with Postgres backend and interactive TUI editing (styled).

Features
- Create Scorecard (1–4 players), unique game_id = "<course> - mm/dd/yyyy[#n]"
- Record Score (menu-driven)
- Show Scorecard (18×4 grid with Front/Back/Total)
- Interactive Scorecard (arrow keys to navigate, 1–8 to set strokes, q to quit)
  * Uses prompt_toolkit styles (no raw ANSI codes) for clean highlighting
- Total Score ("Player -- # holes complete -- total")
- All Scorecards (list)

Dependencies
- psycopg (v3):   pip install "psycopg[binary]"
- prompt_toolkit: pip install prompt_toolkit

DB connection
- Configure via DB_CONN env var, e.g.:
  export DB_CONN="dbname=golf user=<user> host=localhost"
- If not set, a default attempts to use your login user and dbname "golf".
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import List, Tuple

import psycopg

# Lazily import prompt_toolkit pieces; we gate interactive mode if unavailable
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except Exception:  # pragma: no cover
    PROMPT_TOOLKIT_AVAILABLE = False


# ---------- Configuration ----------

def _default_conn_str() -> str:
    # Prefer OS user; fall back to "postgres"
    user = None
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "postgres"
    return f"dbname=golf user={user} host=localhost"

DB_CONN = os.getenv("DB_CONN", _default_conn_str())


# ---------- Schema Bootstrap ----------

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS games (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id TEXT UNIQUE NOT NULL,
    course_name TEXT NOT NULL,
    game_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS players (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id UUID REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id UUID REFERENCES games(id) ON DELETE CASCADE,
    player_id UUID REFERENCES players(id) ON DELETE CASCADE,
    hole_number INT CHECK (hole_number BETWEEN 1 AND 18),
    strokes INT CHECK (strokes BETWEEN 1 AND 8),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (game_id, player_id, hole_number)
);
"""


def init_db() -> None:
    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


# ---------- Utility & Selection ----------

class MenuError(Exception):
    pass


def prompt_int(prompt: str, min_val: int | None = None, max_val: int | None = None) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw:
            print("❌ Input required.")
            continue
        try:
            val = int(raw)
        except ValueError:
            print("❌ Please enter a number.")
            continue
        if min_val is not None and val < min_val:
            print(f"❌ Value must be ≥ {min_val}.")
            continue
        if max_val is not None and val > max_val:
            print(f"❌ Value must be ≤ {max_val}.")
            continue
        return val


def parse_date_mmddyyyy(s: str):
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        raise MenuError("Date must be in mm/dd/yyyy format.")


def list_games() -> List[Tuple[str, str]]:
    """Return list of (id, game_id) for selection, printed to console."""
    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, game_id FROM games ORDER BY created_at DESC")
            rows = cur.fetchall()
    if not rows:
        print("(No games yet)")
        return []
    for idx, row in enumerate(rows, start=1):
        print(f"{idx}. {row[1]}")
    return rows


def select_game() -> Tuple[str, str]:
    rows = list_games()
    if not rows:
        raise MenuError("No games to select.")
    choice = prompt_int("Select game: ", 1, len(rows))
    game_uuid, game_id = rows[choice - 1][0], rows[choice - 1][1]
    return game_uuid, game_id


# ---------- Actions ----------

def create_scorecard() -> None:
    course = input("Enter course name: ").strip()
    if not course:
        print("❌ Course name required.")
        return
    date_str = input("Enter date (mm/dd/yyyy): ").strip()
    try:
        date_obj = parse_date_mmddyyyy(date_str)
    except MenuError as e:
        print(f"❌ {e}")
        return

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            # Ensure unique display game_id
            base_game_id = f"{course} - {date_str}"
            game_id = base_game_id
            suffix = 1
            while True:
                cur.execute("SELECT 1 FROM games WHERE game_id = %s", (game_id,))
                if not cur.fetchone():
                    break
                suffix += 1
                game_id = f"{base_game_id} #{suffix}"

            cur.execute(
                """
                INSERT INTO games (game_id, course_name, game_date)
                VALUES (%s, %s, %s) RETURNING id
                """,
                (game_id, course, date_obj),
            )
            game_uuid = cur.fetchone()[0]

            # Enter 1–4 players
            players_added = 0
            for idx in range(1, 5):
                name = input(f"Enter player {idx} name (blank to stop): ").strip()
                if not name:
                    if players_added == 0 and idx == 1:
                        print("❌ At least one player is required.")
                        conn.rollback()
                        return
                    break
                cur.execute("INSERT INTO players (game_id, name) VALUES (%s, %s)", (game_uuid, name))
                players_added += 1

            if players_added == 0:
                print("❌ No players added. Aborting.")
                conn.rollback()
                return

        conn.commit()
    print(f"✅ Scorecard created: {game_id}")


def record_score() -> None:
    try:
        game_uuid, game_id = select_game()
    except MenuError as e:
        print(f"❌ {e}")
        return

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players WHERE game_id = %s ORDER BY name", (game_uuid,))
            players = cur.fetchall()
            if not players:
                print("❌ No players for this game.")
                return
            for idx, (_, name) in enumerate(players, start=1):
                print(f"{idx}. {name}")
            pchoice = prompt_int("Select player: ", 1, len(players))
            player_id, player_name = players[pchoice - 1]

            hole = prompt_int("Enter hole number (1-18): ", 1, 18)
            strokes = prompt_int("Enter strokes (1-8): ", 1, 8)

            cur.execute(
                """
                INSERT INTO scores (game_id, player_id, hole_number, strokes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (game_id, player_id, hole_number)
                DO UPDATE SET strokes = EXCLUDED.strokes, updated_at = NOW()
                """,
                (game_uuid, player_id, hole, strokes),
            )
        conn.commit()
    print(f"✅ Recorded: {player_name} hole {hole} → {strokes} strokes")


def show_scorecard() -> None:
    try:
        game_uuid, game_id = select_game()
    except MenuError as e:
        print(f"❌ {e}")
        return

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players WHERE game_id = %s ORDER BY name", (game_uuid,))
            players = cur.fetchall()
            if not players:
                print("(No players)")
                return
            player_ids = [p[0] for p in players]
            player_names = [p[1] for p in players]

            header = "Hole | " + " | ".join(f"{n:<8}" for n in player_names)
            print(header)
            print("-" * len(header))

            def fetch_cell(pid, hole):
                cur.execute(
                    "SELECT strokes FROM scores WHERE game_id=%s AND player_id=%s AND hole_number=%s",
                    (game_uuid, pid, hole),
                )
                res = cur.fetchone()
                return str(res[0]) if res else "-"

            for hole in range(1, 19):
                cells = [fetch_cell(pid, hole) for pid in player_ids]
                print(f"{hole:>4} | " + " | ".join(f"{c:<8}" for c in cells))

            # Totals
            def sum_for(pid, holes):
                cur.execute(
                    "SELECT SUM(strokes) FROM scores WHERE game_id=%s AND player_id=%s AND hole_number = ANY(%s)",
                    (game_uuid, pid, list(holes)),
                )
                return cur.fetchone()[0]

            for label, holes in [("Front", range(1, 10)), ("Back", range(10, 19)), ("Total", range(1, 19))]:
                vals = [sum_for(pid, holes) for pid in player_ids]
                display = [str(v) if v is not None else "-" for v in vals]
                print(f"{label:>4} | " + " | ".join(f"{c:<8}" for c in display))


def total_scores() -> None:
    try:
        game_uuid, game_id = select_game()
    except MenuError as e:
        print(f"❌ {e}")
        return

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players WHERE game_id = %s ORDER BY name", (game_uuid,))
            players = cur.fetchall()
            if not players:
                print("(No players)")
                return
            for pid, name in players:
                cur.execute(
                    "SELECT COUNT(strokes), COALESCE(SUM(strokes),0) FROM scores WHERE game_id=%s AND player_id=%s",
                    (game_uuid, pid),
                )
                holes, total = cur.fetchone()
                print(f"{name} -- {holes} holes complete -- {total}")


def all_scorecards() -> None:
    _ = list_games()


# ---------- Interactive TUI (styled) ----------

def interactive_scorecard() -> None:
    """Interactive scorecard using prompt_toolkit styles (no raw ANSI).

    Arrow keys move the selection; 1–8 sets strokes; q quits.
    """
    if not PROMPT_TOOLKIT_AVAILABLE:
        print("❌ Interactive mode requires 'prompt_toolkit'. Install with: pip install prompt_toolkit")
        return

    try:
        game_uuid, game_id = select_game()
    except MenuError as e:
        print(f"❌ {e}")
        return

    # Load players (fixed order during session)
    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players WHERE game_id=%s ORDER BY name", (game_uuid,))
            players = cur.fetchall()
            if not players:
                print("❌ No players for this game.")
                return
    player_ids = [p[0] for p in players]
    player_names = [p[1] for p in players]

    cursor = {"hole": 1, "player": 0}

    # Styles for header, selected cell, and hint text
    style = Style.from_dict({
        "header": "bold",
        "cell.selected": "reverse",
        "hint": "italic dim",
    })

    def render_tokens() -> FormattedText:
        tokens = []  # list of (style, text)
        # Header
        header = "Hole | " + " | ".join(f"{n:<8}" for n in player_names) + "\n"
        tokens.append(("class:header", header))
        tokens.append(("", "-" * (len(header) - 1) + "\n"))

        with psycopg.connect(DB_CONN) as conn2:
            with conn2.cursor() as cur2:
                for hole in range(1, 19):
                    tokens.append(("", f"{hole:>4} | "))
                    for idx, pid in enumerate(player_ids):
                        cur2.execute(
                            "SELECT strokes FROM scores WHERE game_id=%s AND player_id=%s AND hole_number=%s",
                            (game_uuid, pid, hole),
                        )
                        res = cur2.fetchone()
                        val = str(res[0]) if res else "-"
                        cell_text = f"{val:<8}"
                        if hole == cursor["hole"] and idx == cursor["player"]:
                            tokens.append(("class:cell.selected", cell_text))
                        else:
                            tokens.append(("", cell_text))
                        if idx < len(player_ids) - 1:
                            tokens.append(("", " | "))
                    tokens.append(("", "\n"))
        tokens.append(("class:hint", "\n(Use ← → ↑ ↓ to move, keys 1–8 to set strokes, q to quit)\n"))
        return FormattedText(tokens)

    control = FormattedTextControl(text=render_tokens, focusable=True, show_cursor=False)
    window = Window(content=control, wrap_lines=False, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    kb = KeyBindings()

    @kb.add("left")
    def _(event):
        cursor["player"] = max(0, cursor["player"] - 1)
        event.app.invalidate()

    @kb.add("right")
    def _(event):
        cursor["player"] = min(len(player_ids) - 1, cursor["player"] + 1)
        event.app.invalidate()

    @kb.add("up")
    def _(event):
        cursor["hole"] = max(1, cursor["hole"] - 1)
        event.app.invalidate()

    @kb.add("down")
    def _(event):
        cursor["hole"] = min(18, cursor["hole"] + 1)
        event.app.invalidate()

    # Number keys 1–8 to set a score for the current cell
    def make_setter(strokes: int):
        def _set(event):
            with psycopg.connect(DB_CONN) as conn3:
                with conn3.cursor() as cur3:
                    cur3.execute(
                        """
                        INSERT INTO scores (game_id, player_id, hole_number, strokes)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (game_id, player_id, hole_number)
                        DO UPDATE SET strokes=EXCLUDED.strokes, updated_at=NOW()
                        """,
                        (
                            game_uuid,
                            player_ids[cursor["player"]],
                            cursor["hole"],
                            strokes,
                        ),
                    )
                conn3.commit()
            event.app.invalidate()
        return _set

    for i in range(1, 9):
        kb.add(str(i))(make_setter(i))

    @kb.add("q")
    def _(event):
        event.app.exit()

    app = Application(layout=layout, key_bindings=kb, full_screen=True, style=style)
    app.run()


# ---------- Main Loop ----------

def main() -> None:
    try:
        init_db()
    except psycopg.OperationalError as e:
        print("❌ Failed to connect to Postgres.\n     Set DB_CONN or ensure Postgres is running.\n     Example: export DB_CONN=\"dbname=golf user=<you> host=localhost\"")
        print(str(e))
        sys.exit(1)

    while True:
        print("\n--- Golf CLI ---")
        print("1. Create Scorecard")
        print("2. Record Score")
        print("3. Show Scorecard")
        print("4. Interactive Scorecard")
        print("5. Total Score")
        print("6. All Scorecards")
        print("7. Exit")
        choice = input("Select option: ").strip()

        if choice == "1":
            create_scorecard()
        elif choice == "2":
            record_score()
        elif choice == "3":
            show_scorecard()
        elif choice == "4":
            interactive_scorecard()
        elif choice == "5":
            total_scores()
        elif choice == "6":
            all_scorecards()
        elif choice == "7":
            print("Goodbye!")
            break
        else:
            print("❌ Invalid choice")


if __name__ == "__main__":
    main()
