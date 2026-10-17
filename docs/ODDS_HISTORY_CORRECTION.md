# Odds-History Correction — K53 (M3.3 Package C)

Commit `251f07e`. Code: `src/odds/store.py` (`store_quotes`, `actionable_quotes`, `migrate_quote_revisions`, `rollback_quote_revisions`), `src/shadow/cycle._closing`. Tests: `tests/test_odds_history_k53.py` (13), plus the existing odds tests.

## 1. Defect

The `odds_quotes` unique key was (fixture, provider, bookmaker, market, selection, line, provider stamp). It contained no price, and inserts used `INSERT OR IGNORE`. A price that changed while the provider's `update` stamp stayed the same was therefore silently dropped.

## 2. New quote-history identity

**(fixture, provider, bookmaker, market, selection, line, provider stamp `observed_at`, `revision`)**. Every row also keeps our collection time `fetched_at` and the raw market and selection.

| Situation | Behaviour |
|---|---|
| Identical repeated delivery (same stamp, same price as the latest stored under that stamp) | nothing inserted; the first collection time is preserved |
| Same stamp, **changed price** | new row, `revision = previous + 1` |
| A→B→A under the same stamp | three rows (revisions 0, 1, 2); a revert is still recorded |
| New stamp, unchanged price | new row (the provider re-published the price at a new time) |
| Different bookmakers | separate identities |
| Existing rows | never updated or deleted (insert-only) |

## 3. Point-in-time reads

`actionable_quotes(now)` considers only rows with `fetched_at ≤ now` and `observed_at ≤ now`. Per bookmaker selection it takes the newest provider stamp, then the latest collected revision (SQL window function). Freshness and pre-kickoff filters then apply.

Guarantees:
- A price collected after a decision never enters it.
- A replay sees exactly the revision known then (`test_as_of_reads_the_revision_known_at_decision_time`).
- Exactly one row per bookmaker selection is returned, so best-price selection cannot pick an outdated revision.

**Closing reconstruction:** the last pre-kickoff row by (stamp, collection time, revision) that was fetched before kickoff. CLV is defined only when that row is later, by (stamp, collection time), than the price taken (`test_closing_reconstruction_uses_the_last_collected_revision`).

## 4. Migration

- `init_db` → `ensure_odds_tables` → `migrate_quote_revisions`.
- A pre-M3.3 table (no `revision` column) is rebuilt in one transaction:
  1. create the new table;
  2. copy every row unchanged with `revision = 0`. The old key allowed one row per stamp, so no conflict is possible;
  3. verify a SHA-256 checksum over all original columns;
  4. drop the old table and rename the new one.
- Idempotent; a no-op on new tables.
- **Production:** the live DB has no `odds_quotes` yet (M2.5 tables are created at the first approved restart), so it gets the new schema directly.

| Rehearsal (copies) | Rows | Checksum before = after | Integrity | Rollback |
|---|---|---|---|---|
| Shadow DB (`shadow/engine_shadow.db`) | 2,163 | yes | ok | exact (0 revisions archived) |
| Probe DB (`odds_probe/engine_probe.db`) | 3,335 | yes | ok | exact |

## 5. Rollback

`rollback_quote_revisions(conn)` rebuilds the old schema. Rows with `revision > 0` cannot fit the old key, so they are moved unchanged to `odds_quotes_revisions_archive`, never deleted. Tested in `test_rollback_archives_revisions_instead_of_deleting`. Take a backup before either direction.
