import sqlite3

_ALLOWED_TABLES = frozenset({"players", "game_events", "rating_updates", "confidence_updates"})

conn = sqlite3.connect("data/seca.db")
cur = conn.cursor()

for table in sorted(_ALLOWED_TABLES):
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608: names from hardcoded frozenset
        print(table, cur.fetchone()[0])
    except Exception as e:
        print(table, "ERROR:", e)

conn.close()
