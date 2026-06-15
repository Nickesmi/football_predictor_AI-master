"""
Circuit breaker and health logging for data providers.
"""
import time
import uuid
import logging
from datetime import datetime, timezone
from src.db.database import get_db

logger = logging.getLogger("football_predictor")

CIRCUIT_BREAKER_MAX_FAILURES = 3
CIRCUIT_BREAKER_TIMEOUT_SEC = 15 * 60  # 15 minutes

def is_circuit_open(provider: str) -> bool:
    """Check if the provider's circuit is currently open (bypassed) based on historical logs."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT success, created_at FROM provider_health_log WHERE provider = ? ORDER BY id DESC LIMIT ?", 
            (provider, CIRCUIT_BREAKER_MAX_FAILURES)
        ).fetchall()
        
        if len(rows) < CIRCUIT_BREAKER_MAX_FAILURES:
            return False
            
        # Check if all recent attempts failed
        all_failed = all(row["success"] == 0 for row in rows)
        if not all_failed:
            return False
            
        # If all failed, check how recent the last failure was
        most_recent_failure = rows[0]["created_at"]
        
        # SQLite CURRENT_TIMESTAMP is UTC
        # Parse timestamp string to datetime object
        last_failure_dt = datetime.strptime(most_recent_failure, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        
        seconds_since_failure = (now_dt - last_failure_dt).total_seconds()
        
        if seconds_since_failure < CIRCUIT_BREAKER_TIMEOUT_SEC:
            logger.warning(f"Circuit breaker OPEN for {provider}. Bypassing (expires in {int(CIRCUIT_BREAKER_TIMEOUT_SEC - seconds_since_failure)}s).")
            return True
        else:
            logger.info(f"Circuit breaker HALF-OPEN for {provider}. Attempting recovery request.")
            return False
    except Exception as e:
        logger.error(f"Failed to check circuit state for {provider}: {e}")
        # Fail open
        return False

def record_provider_result(provider: str, endpoint: str, success: bool, latency_ms: int, fixture_count: int = 0, odds_count: int = 0, statistics_count: int = 0, lineups_count: int = 0, live_updates: int = 0, error_message: str = None, request_id: str = None):
    """Log the result of a provider request to the DB."""
    if not request_id:
        request_id = str(uuid.uuid4())
        
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO provider_health_log 
            (provider, request_id, endpoint, success, latency_ms, fixture_count, odds_count, statistics_count, lineups_count, live_updates, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (provider, request_id, endpoint, int(success), latency_ms, fixture_count, odds_count, statistics_count, lineups_count, live_updates, error_message))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to write to provider_health_log: {e}")
