import psycopg
from datetime import datetime
import uuid
import os

DB_CONN = os.getenv("DB_CONN", "dbname=golf user=postgres password=postgres host=localhost")

def init_db():
    schema = open("schema.sql").read()
    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute(schema)
        conn.commit()

def create_scorecard():
    course = input("Enter course name: ").strip()
    date_str = input("Enter date (mm/dd/yyyy): ").strip()
    date_obj = datetime.strptime(date_str, "%m/%d/%Y").date()

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            # Check for duplicates and generate game_id
            base_game_id = f"{course} - {date_str}"
            game_id = base_game_id
            suffix = 1
            while True:
                cur.execute("SELECT 1 FROM games WHERE game_id = %s", (game_id,))
                if not cur.fetchone():
                    break
                suffix += 1
                game_id = f"{base_game_id} #{suffix}"

            cur.execute("""
                INSERT INTO games (game_id, course_name, game_date)
                VALUES (%s, %s, %s) RETURNING id
            """, (game_id, course, date_obj))
            game_uuid = cur.fetchone()[0]

            players = []
            while len(players) < 4:
                name = input(f"Enter player {len(players)+1} name (or leave blank to stop): ").strip()
                if not name:
                    break
                cur.execute("INSERT INTO players (game_id, name) VALUES (%s, %s)", (game_uuid, name))
                players.append(name)

            conn.commit()
            print(f"✅ Scorecard created: {game_id} with players {', '.join(players)}")

def list_games():
    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, game_id FROM games ORDER BY created_at DESC")
            rows = cur.fetchall()
            for idx, row in enumerate(rows, start=1):
                print(f"{idx}. {row[1]}")
            return rows

def record_score():
    rows = list_games()
    choice = int(input("Select game: "))
    game_id, game_uuid = rows[choice-1][1], rows[choice-1][0]

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players WHERE game_id = %s", (game_uuid,))
            players = cur.fetchall()
            for idx, (_, name) in enumerate(players, start=1):
                print(f"{idx}. {name}")
            pchoice = int(input("Select player: "))
            player_id, player_name = players[pchoice-1]

            hole = int(input("Enter hole number (1-18): "))
            strokes = int(input("Enter strokes (1-8): "))

            cur.execute("""
                INSERT INTO scores (game_id, player_id, hole_number, strokes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (game_id, player_id, hole_number)
                DO UPDATE SET strokes = EXCLUDED.strokes, updated_at = NOW()
            """, (game_uuid, player_id, hole, strokes))
            conn.commit()
            print(f"✅ Recorded: {player_name} hole {hole} → {strokes} strokes")

def show_scorecard():
    rows = list_games()
    choice = int(input("Select game: "))
    game_id, game_uuid = rows[choice-1][1], rows[choice-1][0]

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            # players
            cur.execute("SELECT id, name FROM players WHERE game_id = %s", (game_uuid,))
            players = cur.fetchall()
            player_ids = [p[0] for p in players]
            player_names = [p[1] for p in players]

            print("Hole | " + " | ".join(f"{n:<8}" for n in player_names))
            print("-" * (6 + 12*len(players)))

            for hole in range(1, 19):
                row = [str(hole).rjust(2)]
                for pid in player_ids:
                    cur.execute("SELECT strokes FROM scores WHERE game_id=%s AND player_id=%s AND hole_number=%s",
                                (game_uuid, pid, hole))
                    res = cur.fetchone()
                    row.append(str(res[0]) if res else "-")
                print(f"{row[0]:>4} | " + " | ".join(f"{r:<8}" for r in row[1:]))

            # Totals
            for label, holes in [("Front", range(1,10)), ("Back", range(10,19)), ("Total", range(1,19))]:
                row = [label]
                for pid in player_ids:
                    cur.execute("SELECT SUM(strokes) FROM scores WHERE game_id=%s AND player_id=%s AND hole_number=ANY(%s)",
                                (game_uuid, pid, list(holes)))
                    total = cur.fetchone()[0]
                    row.append(str(total) if total else "-")
                print(f"{row[0]:>4} | " + " | ".join(f"{r:<8}" for r in row[1:]))

def total_scores():
    rows = list_games()
    choice = int(input("Select game: "))
    game_id, game_uuid = rows[choice-1][1], rows[choice-1][0]

    with psycopg.connect(DB_CONN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players WHERE game_id = %s", (game_uuid,))
            players = cur.fetchall()
            for pid, name in players:
                cur.execute("SELECT COUNT(strokes), COALESCE(SUM(strokes),0) FROM scores WHERE game_id=%s AND player_id=%s",
                            (game_uuid, pid))
                holes, total = cur.fetchone()
                print(f"{name} -- {holes} holes complete -- {total}")

def all_scorecards():
    list_games()

def main():
    init_db()
    while True:
        print("\n--- Golf CLI ---")
        print("1. Create Scorecard")
        print("2. Record Score")
        print("3. Show Scorecard")
        print("4. Total Score")
        print("5. All Scorecards")
        print("6. Exit")

        choice = input("Select option: ").strip()
        if choice == "1":
            create_scorecard()
        elif choice == "2":
            record_score()
        elif choice == "3":
            show_scorecard()
        elif choice == "4":
            total_scores()
        elif choice == "5":
            all_scorecards()
        elif choice == "6":
            break
        else:
            print("❌ Invalid choice")

if __name__ == "__main__":
    main()
