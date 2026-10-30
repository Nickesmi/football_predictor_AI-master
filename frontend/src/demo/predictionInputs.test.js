import test from 'node:test';
import assert from 'node:assert/strict';
import { predictionAvailability } from './predictionInputs.js';

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
