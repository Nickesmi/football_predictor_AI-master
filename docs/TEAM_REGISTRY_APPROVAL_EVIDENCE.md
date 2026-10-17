# Team Registry — Approval Evidence (M3.3 Package A, items 8–10)

Registry: `src/identity/team_registry.v1.json` (commit `dbea35d`). Each entry now carries `audit_m3_3` (rh1 status, rh1 matches, rh1 raw names, team_state records, identity-v1 status with the entry) and a `review` block.

## Acceptance criteria (owner-delegated, Package A9)

A **map** entry is accepted only if **all** of the following hold:
1. The mapped `team_state` record verifies: every stored source fixture carries the competition's league ids.
2. The rh1 reconstruction resolves the same provider name in the same competition from the competition's own fixtures.
3. The rh1 key has no collision.
4. The rh1 raw names are exactly the provider name.

An **exclusion** is confirmed if the `team_state` record is contaminated, unverifiable or absent.

All entries are marked `review.status = "approved"` with `approval_basis`, `approved_by = "M3.3 evidence audit"` and `owner_confirmation = "pending"`. **Owner confirmation is still requested.** Reverting is one field per entry. `registry.load_as_proposed()` reproduces the M3.2 state, and the M3.2 identity golden passes unchanged that way.

## Result: 18 maps accepted, 13 exclusions confirmed (31/31)

| Entry | Decision | team_state path (identity-v1) | rh1 (reconstructed) |
|---|---|---|---|
| SCO-DUNDEE, SCO-STMIRREN, SCO-STJOHNSTONE | map ✔ | RESOLVED via entry | RESOLVED: 4 / 3 / 4 matches |
| NED-TELSTAR, NED-GRONINGEN, NED-UTRECHT, NED-AJAX, NED-HEERENVEEN, NED-TWENTE | map ✔ | RESOLVED | RESOLVED: 6 / 5 / 6 / 5 / 5 / 7 |
| BEL-STANDARD | map ✔ | RESOLVED | RESOLVED: 3 |
| POR-AROUCA, POR-ALVERCA, POR-NACIONAL, POR-FAMALICAO | map ✔ | RESOLVED | RESOLVED: 3 / 7 / 7 / 5 |
| ESP-BARCELONA, ESP-ATLETICO | map ✔ | RESOLVED | RESOLVED: 2 / 4 |
| ENG-BRIGHTON, FRA-PSG (same club, two records) | map ✔ | RESOLVED (record with more matches) | RESOLVED: 4 / 1 |
| ENG-ARSENAL | exclude ✔ | CONTAMINATED_HISTORY (7/8 fixtures from 116) | RESOLVED: **1 clean match** (39 only) |
| FRA-LENS, FRA-MONACO, FRA-STRASBOURG, FRA-MARSEILLE, FRA-BREST | exclude ✔ | no verifiable record (sofascore-named rows without source fixtures) | RESOLVED: 3 / 3 / 4 / 3 / 2 |
| FRA-LYON, ITA-INTER, UEL-OFI, TUR-ALANYASPOR, UCL-VIKING | exclude ✔ | no record | RESOLVED: 3 / 4 / 2 / 2 / 1 |
| SCO-DUNDEEUTD, SCO-RANGERS | exclude ✔ | unverifiable record | RESOLVED: 5 / 4 |

**Reading:**
- The exclusions stay excluded on the `team_state` path, because those records remain contaminated or unverifiable.
- On the rh1 path they are **not needed**: each team resolves from its own competition's fixtures by exact normalised name, with no alias and no `team_state` record.
- No unverified alias was added anywhere.

## Provider team ids

API-Football team ids are not in the stored data, and fetching them would cost requests. Entries keep `provider_team_id = null`. The first identity-checked resolution that carries an id binds it in `team_identity_map` (source `registry:<id>`). A later mismatch fails closed (`test_provider_id_binding_mismatch_fails_closed`).
