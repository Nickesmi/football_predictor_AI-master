"""
Competition tracker — records every league/competition encountered by the platform.
Populated automatically as fixtures are fetched and cached.
"""
from __future__ import annotations
import sqlite3
import logging
from datetime import date

logger = logging.getLogger("football_predictor")


def ensure_competitions_table(conn: sqlite3.Connection) -> None:
    """Create competitions table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS competitions (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            country         TEXT DEFAULT '',
            category        TEXT DEFAULT '',  -- 'men', 'women', 'youth', 'international', 'friendly'
            first_seen      DATE,
            last_seen       DATE,
            total_fixtures  INTEGER DEFAULT 0,
            logo_url        TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_competitions_country
        ON competitions(country)
    """)
    conn.commit()


def _infer_category(name: str, country: str) -> str:
    """Infer competition category from name."""
    n = name.lower()
    if any(w in n for w in ['women', 'female', 'ladies', 'wsl', 'wfc']):
        return 'women'
    if any(w in n for w in ['u17', 'u18', 'u19', 'u20', 'u21', 'u23', 'youth', 'under']):
        return 'youth'
    if any(w in n for w in ['friendly', 'friendlies']):
        return 'friendly'
    if country in ('World', 'Europe', 'South America', 'Asia', 'Africa', 'North America', 'Oceania'):
        return 'international'
    return 'men'


def upsert_competition(
    conn: sqlite3.Connection,
    league_id: str,
    name: str,
    country: str,
    logo_url: str = '',
) -> None:
    """Insert or update a competition record. Called when fixtures are written."""
    try:
        ensure_competitions_table(conn)
        today = date.today().isoformat()
        category = _infer_category(name, country)
        conn.execute("""
            INSERT INTO competitions (id, name, country, category, first_seen, last_seen, total_fixtures, logo_url)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_seen     = excluded.last_seen,
                total_fixtures = total_fixtures + 1,
                name          = excluded.name,
                country       = excluded.country,
                logo_url      = CASE WHEN excluded.logo_url != '' THEN excluded.logo_url ELSE logo_url END
        """, (league_id, name, country, category, today, today, logo_url))
        conn.commit()
    except Exception as e:
        logger.warning(f"Competition upsert failed for {league_id}/{name}: {e}")


def get_competition_stats(conn: sqlite3.Connection) -> dict:
    """Return summary stats about all tracked competitions."""
    try:
        ensure_competitions_table(conn)
        rows = conn.execute("SELECT category, COUNT(*) as cnt FROM competitions GROUP BY category").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM competitions").fetchone()[0]
        by_cat = {r['category']: r['cnt'] for r in rows}
        return {
            "total_competitions": total,
            "men": by_cat.get('men', 0),
            "women": by_cat.get('women', 0),
            "youth": by_cat.get('youth', 0),
            "international": by_cat.get('international', 0),
            "friendly": by_cat.get('friendly', 0),
        }
    except Exception as e:
        logger.warning(f"Competition stats failed: {e}")
        return {}


def list_competitions(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """List all tracked competitions ordered by total fixtures desc."""
    try:
        ensure_competitions_table(conn)
        rows = conn.execute("""
            SELECT id, name, country, category, first_seen, last_seen, total_fixtures, logo_url
            FROM competitions
            ORDER BY total_fixtures DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"List competitions failed: {e}")
        return []
