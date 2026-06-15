import sqlite3
from src.db.database import get_db

conn = get_db()
cursor = conn.cursor()

# Try to add columns to matches
for col, ctype in [
    ("provider", "TEXT DEFAULT 'sofascore'"),
    ("data_quality_score", "REAL"),
    ("is_main_fixture", "BOOLEAN"),
    ("is_stale", "BOOLEAN DEFAULT 0"),
    ("provider_error", "TEXT"),
    ("last_live_update", "TIMESTAMP")
]:
    try:
        cursor.execute(f"ALTER TABLE matches ADD COLUMN {col} {ctype}")
        print(f"Added column {col}")
    except sqlite3.OperationalError as e:
        print(f"Column {col} might exist: {e}")

# Create pending_settlements
cursor.execute("""
CREATE TABLE IF NOT EXISTS pending_settlements (
    event_id TEXT PRIMARY KEY,
    first_ft_seen TIMESTAMP NOT NULL,
    provider TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed BOOLEAN DEFAULT 0
)
""")
print("Created pending_settlements table")

conn.commit()
print("Migration complete.")
