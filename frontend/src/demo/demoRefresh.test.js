// node --test src/demo   (no extra dependencies)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  allowBackgroundRefresh, allowanceText, confirmText, dataBadge, formatUpdated, isDemo, refreshControl, refreshOutcome,
} from './demoRefresh.js';

const exhausted = {
  mode: 'demo', cost_per_refresh: 1, can_refresh: false, remaining_today: 0, remaining_total: 0,
  legacy: { cap: 5, used: 5, remaining: 0 },
  blocked_reasons: ['no owner-authorized allowance (scripts/demo_allowance.py grant)'],
};
const granted = { ...exhausted, can_refresh: true, remaining_today: 3, remaining_total: 20, blocked_reasons: [] };
const NOW = Date.parse('2026-10-23T12:00:00Z');

test('outside demo mode nothing is shown and background refresh stays as before', () => {
  assert.equal(isDemo(null), false);
  assert.deepEqual(refreshControl(null, null), { visible: false, enabled: false });
  assert.equal(allowBackgroundRefresh(null), true);
});

test('demo mode never refreshes in the background (page focus, polling)', () => {
  assert.equal(allowBackgroundRefresh(exhausted), false);
  assert.equal(allowBackgroundRefresh(granted), false);
});

test('exhausted allowance: refresh disabled with the reason; cached fixtures stay', () => {
  const c = refreshControl(exhausted, { cached: false });
  assert.equal(c.visible, true);
  assert.equal(c.enabled, false);
  assert.match(c.reason, /no owner-authorized allowance/);
  assert.match(c.reason, /Cached fixtures stay visible/);
  assert.equal(c.label, 'Refresh fixtures (1 request)');
});

test('authorized allowance: enabled, shows cost and remaining before any request', () => {
  const c = refreshControl(granted, { cached: false });
  assert.equal(c.enabled, true);
  assert.equal(c.detail, '3 left today · 20 left in authorized allowance');
  assert.match(confirmText(granted, '2026-10-23'), /Cost: 1 request\.\nRemaining after this: 2 today, 19/);
});

test('an already cached date is never requested again', () => {
  const c = refreshControl(granted, { cached: true });
  assert.equal(c.enabled, false);
  assert.match(c.reason, /already cached/);
});

test('a running refresh disables the control (no double click)', () => {
  const c = refreshControl(granted, { cached: false }, { busy: true });
  assert.equal(c.enabled, false);
  assert.equal(c.label, 'Refreshing…');
});

test('cached data is labelled as a snapshot with its update time, never as live', () => {
  const today = dataBadge({ cached: true, period: 'today', last_updated_at: '2026-10-23T09:00:00Z' }, NOW);
  assert.equal(today.kind, 'cached');
  assert.match(today.text, /^Cached snapshot · not live · updated 3 h ago \(/);
  assert.match(dataBadge({ cached: true, period: 'past', last_updated_at: '2026-10-20T09:00:00Z' }, NOW).text,
    /^Cached results snapshot · updated 3 days ago/);
  assert.equal(dataBadge({ cached: false }).kind, 'none');
  assert.equal(dataBadge({ cached: true, period: 'today', last_updated_at: null }).text,
    'Cached snapshot · not live · update time unknown');
  assert.equal(formatUpdated(null), 'never updated');
});

test('legacy allowance, when any is left, is shown separately', () => {
  assert.match(allowanceText({ ...granted, legacy: { cap: 5, used: 3, remaining: 2 } }), /^2 legacy · /);
});

test('refresh outcomes produce a readable message for every status', () => {
  assert.deepEqual(refreshOutcome(200, { cache: { cached_fixtures: 12 } }), { ok: true, text: 'Fixtures updated: 12 cached.' });
  assert.equal(refreshOutcome(409, { detail: { message: 'x is already cached; no request sent.' } }).text,
    'x is already cached; no request sent.');
  assert.equal(refreshOutcome(403, { detail: {} }).ok, false);
  assert.match(refreshOutcome(502, { detail: { message: 'Refresh did not return fixtures: HTTP Error 429' } }).text, /429/);
  assert.match(refreshOutcome(500, null).text, /cached fixtures are unchanged/);
});
