#!/usr/bin/env python3
"""
Golf Scorecard CLI with Postgres backend, interactive TUI, and Golf Course API integration.
- Auto-loads .env via python-dotenv
- Robust Golf Course API search that tries common auth schemes and paths
- Interactive score entry with arrow keys (prompt_toolkit styles)

Menu
1. Create Scorecard
2. Record Score
3. Show Scorecard
4. Interactive Scorecard
5. Total Score
6. All Scorecards
7. Search Golf Courses
8. Exit

Setup
- pip install "psycopg[binary]" prompt_toolkit requests python-dotenv
- .env example:
    GOLF_API_KEY=YOUR_REAL_KEY_HERE
    DB_CONN=dbname=golf user=<you> host=localhost
- If your API uses /api/v1, set:
    GOLF_API_SEARCH_PATH=/api/v1/courses/search
  (default is /v1/courses/search)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, date
from typing import List, Tuple, Dict, Any

import requests
import psycopg
from dotenv import load_dotenv

# Load environment variables from .env automatically
load_dotenv()

# ---------- prompt_toolkit (optional, for interactive mode) ----------
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except Exception:
    PROMPT_TOOLKIT_AVAILABLE = False

# ---------- Configuration ----------

def _default_conn_str() -> str:
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "postgres"
    return f"dbname=golf user={user} host=localhost"

DB_CONN = os.getenv("DB_CONN", _default_conn_str())

# Golf Course API config
GOLF_API_KEY = os.getenv("GOLF_API_KEY")
GOLF_API_BASE = os.getenv("GOLF_API_BASE", "https://api.golfcourseapi.com")
GOLF_API_SEARCH_PATH = os.getenv("GOLF_API_SEARCH_PATH", "/v1/courses/search")  # try "/api/v1/courses/search" if you get 404
GOLF_API_DEBUG = os.getenv("GOLF_API_DEBUG", "0") == "1"  # set to 1 to print which auth/path succeeded


def _api_url(path: str) -> str:
    base = GOLF_API_BASE.rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return f"{base}{p}"

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


def prompt_with_default(prompt: str, default_val: str) -> str:
    raw = input(f"{prompt} [{default_val}]: ").strip()
    return raw or default_val


def parse_date_mmddyyyy(s: str) -> date:
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

# ---------- Golf Course API Integration ----------

def search_courses_by_name(term: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search courses by name, trying common auth schemes and paths.

    Tries in order:
      - Authorization: Key <API_KEY>
      - Authorization: Bearer <API_KEY>
      - x-api-key: <API_KEY>
      - query string api_key=<API_KEY>
    And tries both /v1/courses/search and /api/v1/courses/search unless overridden.
    """
    if not GOLF_API_KEY:
        print("❌ GOLF_API_KEY not set. Put it in .env or export it.")
        return []

    paths = [GOLF_API_SEARCH_PATH or "/v1/courses/search", "/api/v1/courses/search"]
    # De-dup while preserving order
    seen = set(); ordered_paths = []
    for p in paths:
        if p not in seen:
            ordered_paths.append(p); seen.add(p)

    auth_styles = ["key", "bearer", "x-api-key", "query"]

    for path in ordered_paths:
        url = _api_url(path)
        base_params = {"q": term, "limit": limit}
        for style in auth_styles:
            headers = {"Accept": "application/json", "User-Agent": "golf-cli/1.0"}
            params = dict(base_params)
            if style == "key":
                headers["Authorization"] = f"Key {GOLF_API_KEY}"
            elif style == "bearer":
                headers["Authorization"] = f"Bearer {GOLF_API_KEY}"
            elif style == "x-api-key":
                headers["x-api-key"] = GOLF_API_KEY
            elif style == "query":
                params["api_key"] = GOLF_API_KEY

            try:
                resp = requests.get(url, headers=headers, params=params, timeout=12)
                # 404 usually means wrong path; try next path
                if resp.status_code == 404:
                    if GOLF_API_DEBUG:
                        print(f"[debug] 404 on {url} with {style}; trying alternate path…")
                    break
                # 401 means auth scheme likely wrong; try next style
                if resp.status_code == 401:
                    if GOLF_API_DEBUG:
                        print(f"[debug] 401 on {url} with {style}; trying another auth scheme…")
                    continue
                resp.raise_for_status()
                data = resp.json() if resp.content else {}
                if GOLF_API_DEBUG:
                    print(f"[debug] ✅ success: path={path} auth={style}")
                # Normalize to a list of dicts
                raw_courses = data if isinstance(data, list) else (data.get("courses") or data.get("results") or [])
                courses: List[Dict[str, Any]] = []
                for c in raw_courses:
                    courses.append({
                        "id": c.get("id") or c.get("_id") or c.get("course_id"),
                        "name": c.get("name") or c.get("course_name") or "(Unnamed Course)",
                        "city": c.get("city") or c.get("town") or "",
                        "state": c.get("state") or c.get("region") or "",
                        "country": c.get("country") or "",
                    })
                return courses
            except requests.RequestException as e:
                if GOLF_API_DEBUG:
                    print(f"[debug] exception on {url} with {style}: {e}")
                continue

    print("❌ API request failed: tried multiple auth styles (Key/Bearer/x-api-key/query) and paths (/v1, /api/v1) but all were unauthorized or not found.")
    print("   Tips: verify your key, header scheme, and correct path in the API docs/dashboard.")
    return []


def search_courses_interactive() -> None:
    term = input("Search course name: ").strip()
    if not term:
        print("❌ Course name is required.")
        return

    results = search_courses_by_name(term, limit=10)
    if not results:
        print("No courses found.")
        return

    print(f"\nResults for '{term}':")
    for idx, c in enumerate(results, start=1):
        loc_bits = [c.get("city") or "", c.get("state") or "", c.get("country") or ""]
        loc = ", ".join([b for b in loc_bits if b])
        print(f"{idx}. {c['name']}{f' — {loc}' if loc else ''}")

    use = input("\nUse one of these to prefill Create Scorecard? (y/N): ").strip().lower()
    if use != "y":
        return

    pick = prompt_int("Select course number: ", 1, len(results))
    picked_name = results[pick - 1]["name"]
    create_scorecard(prefill_course=picked_name)

# ---------- Actions ----------

def create_scorecard(prefill_course: str | None = None) -> None:
    if prefill_course:
        course = prompt_with_default("Enter course name", prefill_course).strip()
    else:
        course = input("Enter course name: ").strip()
        if not course:
            print("❌ Course name required.")
            return

    today_str = date.today().strftime("%m/%d/%Y")
    date_str = prompt_with_default("Enter date (mm/dd/yyyy)", today_str)
    try:
        date_obj = parse_date_mmddyyyy(date_str)
    except MenuError as e:
        print(f"❌ {e}")
        return

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
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
    if not PROMPT_TOOLKIT_AVAILABLE:
        print("❌ Interactive mode requires 'prompt_toolkit'. Install with: pip install prompt_toolkit")
        return

    try:
        game_uuid, game_id = select_game()
    except MenuError as e:
        print(f"❌ {e}")
        return

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

    style = Style.from_dict({
        "header": "bold",
        "cell.selected": "reverse",
        "hint": "italic dim",
    })

    def render_tokens() -> FormattedText:
        tokens = []
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
        print("7. Search Golf Courses")
        print("8. Exit")
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
            search_courses_interactive()
        elif choice == "8":
            print("Goodbye!")
            break
        else:
            print("❌ Invalid choice")

if __name__ == "__main__":
    main()
