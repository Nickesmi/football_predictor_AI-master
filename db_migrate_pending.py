import sqlite3
from src.db.database import get_db

conn = get_db()
cursor = conn.cursor()

for col, ctype in [
    ("attempts", "INTEGER DEFAULT 0"),
    ("last_check", "TIMESTAMP")
]:
    try:
        cursor.execute(f"ALTER TABLE pending_settlements ADD COLUMN {col} {ctype}")
        print(f"Added column {col}")
    except sqlite3.OperationalError as e:
        print(f"Column {col} might exist: {e}")

conn.commit()
print("Migration complete.")
