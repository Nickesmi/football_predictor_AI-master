import test from 'node:test';
import assert from 'node:assert/strict';
import { identityQuery, predictionAvailability } from './predictionInputs.js';

test('a team without real data makes the prediction unavailable', () => {
  const a = predictionAvailability({ missing_inputs: [
    "home_team 'Wrexham AFC' has NO real data — using fallback defaults",
    "league 'Championship' uses fallback Poisson profile"] });
  assert.equal(a.available, false);
  assert.deepEqual(a.blocking, ["home_team 'Wrexham AFC' has NO real data — using fallback defaults"]);
  assert.deepEqual(a.warnings, ["league 'Championship' uses fallback Poisson profile"]);
});

test('thin data is a warning; no notes (or an old response) is available', () => {
  assert.deepEqual(predictionAvailability({ missing_inputs: ["away_team 'X' has only 3 matches"] }),
    { available: true, blocking: [], warnings: ["away_team 'X' has only 3 matches"] });
  assert.equal(predictionAvailability({}).available, true);
  assert.equal(predictionAvailability(null).available, true);
});

test('a fail-closed analysis (prediction_available false) is unavailable', () => {
  assert.equal(predictionAvailability({ prediction_available: false, missing_inputs: [] }).available, false);
});

test('the analysis request carries the provider ids for identity resolution', () => {
  const f = { provider: 'api_football', source: 'api_football', league_id_apifootball: '140',
    league: { id: '140', country: 'Spain' }, home_team: { id: '541' }, away_team: { id: '797' },
    kickoff_utc: '2026-11-01T15:15:00Z' };
  assert.equal(identityQuery(f), '&provider=api_football&league_id=140&country=Spain&home_id=541&away_id=797'
    + '&kickoff_utc=2026-11-01T15%3A15%3A00Z');
  const fd = { provider: 'football_data', source: 'football_data', league: { id: 'fd-2014', code: 'PD', country: 'Spain' },
    home_team: { id: 'fd-86' }, away_team: { id: 'fd-1' } };
  assert.match(identityQuery(fd), /^&provider=football_data&league_id=fd-2014&league_code=PD&/);
  assert.equal(identityQuery({}), '');
});

test('a history record that already contains results from the kick-off on blocks the prediction', () => {
  const leak = "away_team 'Inter' history record 'Serie A' already includes results dated 2026-11-03, on or after "
    + "this match's kick-off date 2026-11-03; no point-in-time record is available, so it is not used";
  const a = predictionAvailability({ prediction_available: false, missing_inputs: [leak, "home_team 'X' has only 3 matches"] });
  assert.equal(a.available, false);
  assert.deepEqual(a.blocking, [leak]);
});

test('cross-competition notes on an available prediction stay warnings', () => {
  const notes = ["home_team 'Feyenoord' uses its domestic 'Eredivisie' history (8 matches); it has 2 verified "
    + "'uefa-champions-league' matches",
  "home and away statistics come from different competitions ('Eredivisie', 'Serie A'); the model applies no "
    + "league-strength adjustment"];
  assert.deepEqual(predictionAvailability({ prediction_available: true, missing_inputs: notes }),
    { available: true, blocking: [], warnings: notes });
});
