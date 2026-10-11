"""
Regression tests for Blocker 2 (settlement operational readiness):

- scripts/settlement_worker.py's __main__ must run once and exit — it was
  previously an infinite `while True: ...; time.sleep(300)` loop, which
  conflicts with its own systemd unit (Type=oneshot,
  scripts/orchestration/systemd/settlement_worker.service) and with the hourly
  crontab.txt entry, both of which invoke it periodically themselves and
  expect a single execution per trigger.
- scripts.settlement_worker.run_settlement() — the actual function either
  scheduling mechanism calls — must carry a pick from pending to a terminal
  state (won/lost) via the real, scheduled entrypoint, not just via
  auto_settle_picks() called directly in isolation.
"""
import ast

from src.db.picks_repo import insert_pick


def test_settlement_worker_main_block_runs_once_and_exits():
    """Confirms the __main__ block contains no loop construct — a static check
    since actually invoking the module as __main__ would require a live
    process. This is the exact class of bug that made settlement silently
    never resolve postponed/cancelled picks when scheduled via cron/systemd."""
    source = open("scripts/settlement_worker.py").read()
    tree = ast.parse(source)

    main_block = None
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            main_block = node
            break

    assert main_block is not None, "no `if __name__ == '__main__':` block found"
    for stmt in ast.walk(main_block):
        assert not isinstance(stmt, (ast.While, ast.For)), (
            "settlement_worker.py's __main__ block must not loop — both its "
            "crontab.txt entry and its Type=oneshot systemd service expect a "
            "single execution per scheduled trigger"
        )


def test_run_settlement_carries_a_pending_pick_to_a_terminal_state(engine_db, monkeypatch):
    """Exercises the actual scheduled entrypoint (run_settlement()), not
    auto_settle_picks() directly, proving the real production path — as either
    cron or systemd would invoke it — resolves a pending pick."""
    import scripts.settlement_worker as settlement_worker

    monkeypatch.setattr(settlement_worker, "get_db", lambda: engine_db)
    monkeypatch.setattr(settlement_worker, "fetch_daily_matches", lambda date_str: ([], None))

    engine_db.execute(
        """INSERT INTO matches (id, date, home_team, away_team, league_name, status, home_goals, away_goals)
           VALUES ('m1', '2026-01-01', 'Home FC', 'Away FC', 'Test League', 'FT', 2, 0)"""
    )
    pick_id = insert_pick(engine_db, {
        "match_id": "m1",
        "market": "1X2",
        "selection": "home",
        "model_prob": 0.6,
        "implied_prob": 0.5,
        "edge": 0.1,
        "odds_at_pick": 2.0,
        "confidence": 0.7,
        "league_reliability": 8.0,
        "grade": "A",
        "stake_units": 1.0,
        "model_source": "poisson",
    })
    engine_db.commit()

    settlement_worker.run_settlement()

    row = engine_db.execute(
        "SELECT result, pnl_units FROM picks WHERE id = ?", (pick_id,)
    ).fetchone()
    assert row["result"] == "won"
    assert row["pnl_units"] == 1.0  # stake(1.0) * (odds(2.0) - 1)
