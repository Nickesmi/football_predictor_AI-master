# Team Identity Mapping Review — 31 ambiguous or unresolved teams (M3.2, Decision 2)

Registry: `src/identity/team_registry.v1.json` (`team-registry-v1`), loader and rules in `src/identity/registry.py` (commit `afb7bc4`). Tests: `tests/test_team_registry.py` (18).

## Rules

- Each entry is keyed by **competition** (provider league ids) and **normalised provider team name**.
- An entry is either **map** (to one `team_state` record) or **exclude** (stay fail-closed).
- Every entry carries its evidence, a reason and a review state.
- **All 31 entries are `proposed`. The resolver ignores them** until the owner sets `review.status = "approved"`. Isolated comparisons can load them with `allow_proposed=True`; the pipeline and shadow runner never do.
- A mapped record is re-verified on every use: every stored source fixture must carry the competition's league ids. A later contaminating fixture turns the mapping into `CONTAMINATED_HISTORY`.
- **Provider team ids:** API-Football team ids are not in the stored data. `/fixtures` responses carry them, but fetching them would need extra requests, which were not approved. So entries start with `provider_team_id = null`.
  - At the first identity-checked resolution that carries a provider id (shadow discovery now stores them), the binding is written to `team_identity_map` with source `registry:<id>`.
  - After that, a different id for the same entry fails closed (`AMBIGUOUS`, "provider team id differs").
- **No unverified alias is mapped.** A record whose source fixtures cannot be found is never the target of a mapping, however obvious the name looks.

## Verification method (each entry)

For each team, every `team_state` record in the competition's labels with the same normalised name (plus near names, for review only) was listed. For each record:
- matches played;
- the league ids of every stored fixture behind it (normalised name, same label);
- the provider fixtures of the team in the competition.

Run on a copy of the 2026-10-16 backup. Every "map" entry resolves to `RESOLVED` with `allow_proposed=True`, and every entry stays as before without it (verified 31/31).

## Decisions

### Map — one competition-verified record; the other record has no source fixtures (16)

| Entry | Competition | Provider name | Mapped record (label, matches) | Verified source fixtures | Unused record |
|---|---|---|---|---|---|
| SCO-DUNDEE | Scotland Premiership | Dundee | Dundee (Premiership, 8) | 4 × league 179 | Dundee FC (Scottish Premiership, 1) |
| SCO-STMIRREN | Scotland Premiership | ST Mirren | ST Mirren (Premiership, 3) | 3 × 179 | St. Mirren (1) |
| SCO-STJOHNSTONE | Scotland Premiership | ST Johnstone | ST Johnstone (Premiership, 4) | 4 × 179 | St. Johnstone (1) |
| NED-TELSTAR | Eredivisie | Telstar | Telstar (Eredivisie, 5) | 6 × 88 | SC Telstar (1) |
| NED-GRONINGEN | Eredivisie | Groningen | Groningen (Eredivisie, 4) | 5 × 88 | FC Groningen (1) |
| NED-UTRECHT | Eredivisie | Utrecht | Utrecht (Eredivisie, 6) | 6 × 88 | FC Utrecht (1) |
| NED-AJAX | Eredivisie | Ajax | Ajax (Eredivisie, 3) | 5 × 88 | AFC Ajax (1) |
| NED-HEERENVEEN | Eredivisie | Heerenveen | Heerenveen (Eredivisie, 4) | 5 × 88 | SC Heerenveen (1) |
| NED-TWENTE | Eredivisie | Twente | Twente (Eredivisie, 7) | 7 × 88 | FC Twente (1) |
| BEL-STANDARD | Belgian Pro League | Standard Liege | Standard Liege (Jupiler Pro League, 3) | 3 × 144 | Standard Liège (Pro League, 2) |
| POR-AROUCA | Primeira Liga | Arouca | Arouca (Primeira Liga, 2) | 3 × 94 | FC Arouca (1) |
| POR-ALVERCA | Primeira Liga | Alverca | Alverca (Primeira Liga, 7) | 7 × 94 | FC Alverca (1) |
| POR-NACIONAL | Primeira Liga | Nacional | Nacional (Primeira Liga, 5) | 7 × 94 | CD Nacional (1) |
| POR-FAMALICAO | Primeira Liga | Famalicao | Famalicao (Primeira Liga, 5) | 5 × 94 | Famalicão (1) |
| ESP-BARCELONA | La Liga | Barcelona | Barcelona (La Liga, 2) | 2 × 140 | FC Barcelona (LaLiga, 1) |
| ESP-ATLETICO | La Liga | Atletico Madrid | Atletico Madrid (La Liga, 4) | 5 × 140 | Atlético Madrid (LaLiga, 1) |

### Map — same club, two verified records (2)

| Entry | Provider name | Mapped record | Other record | Note |
|---|---|---|---|---|
| ENG-BRIGHTON | Brighton | Brighton (Premier League, 4) | Brighton & Hove Albion (2) | Histories are not merged; the record with more matches is used |
| FRA-PSG | Paris Saint Germain | Paris Saint-Germain (Ligue 1, 2) | Paris Saint Germain (1) | same |

### Exclude — stays fail-closed (13)

| Entry | Provider name | Reason |
|---|---|---|
| FRA-LENS, FRA-MONACO, FRA-STRASBOURG, FRA-MARSEILLE, FRA-BREST | Lens, Monaco, Strasbourg, Marseille, Stade Brestois 29 | The only records ("RC Lens", "AS Monaco", "RC Strasbourg", "Olympique de Marseille", "Stade Brestois") have **no source fixtures**, so an alias would be unverified. The "Ligue 1" label also holds Algerian, Tunisian and Ivorian clubs. |
| FRA-LYON, ITA-INTER, UEL-OFI, TUR-ALANYASPOR, UCL-VIKING | Lyon, Inter, OFI, Alanyaspor, Viking | No history record in the competition labels. The legacy LIKE fallback would give Inter the record of **Internacional (Brazil)** in the shared "Serie A" label. |
| SCO-DUNDEEUTD | Dundee Utd | Only "Dundee United" (1 match, no source fixtures) |
| SCO-RANGERS | Rangers | Only "Rangers" (Scottish Premiership, 1 match, no source fixtures). "Carrick Rangers" is a Northern Irish club (league 408). |
| ENG-ARSENAL | Arsenal | **Contaminated:** 7 of the 8 source fixtures of the English "Premier League" record come from competition 116, another country's club named Arsenal. **Production uses this record for Arsenal today.** It must be rebuilt per competition before any use. |

## How the exclusions can be cleared (all need approval)

1. **Rebuild `team_state` per competition id** (K49) from stored fixtures. This would separate Arsenal (England) from the other Arsenal and give the sofascore-named French records verifiable sources. It is a data rebuild that changes probability inputs.
2. **Obtain provider team ids** for the excluded teams from `/fixtures` responses during an approved shadow run (no extra requests; the ids come with discovery), and record their competition evidence.
3. **Owner approval** of each "map" entry (`review.status = "approved"`, with the reviewer recorded).

## Coverage with and without the registry (51 upcoming tracked fixtures)

| | Fixtures with both identities resolved |
|---|---|
| team-identity-v1 alone | 39 / 51 |
| with the 18 proposed maps (isolated comparison only) | 44 / 51 |
| still fail-closed (excluded teams) | 7 |
