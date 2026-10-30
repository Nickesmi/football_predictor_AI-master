// node --test src/demo   (no extra dependencies)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  allowBackgroundRefresh, allowanceText, confirmText, dataBadge, dateInZone, formatUpdated, isDemo, maxLoadRetries,
  refreshControl, refreshOutcome, timesNote,
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
  const c = refreshControl(exhausted, { fetched: false });
  assert.equal(c.visible, true);
  assert.equal(c.enabled, false);
  assert.match(c.reason, /no owner-authorized allowance/);
  assert.match(c.reason, /Cached fixtures stay visible/);
  assert.equal(c.label, 'Refresh fixtures (1 request)');
});

test('authorized allowance: enabled, shows cost and remaining before any request', () => {
  const c = refreshControl(granted, { fetched: false });
  assert.equal(c.enabled, true);
  assert.equal(c.detail, '3 left today · 20 left in authorized allowance');
  assert.match(confirmText(granted, '2026-10-23'), /Cost: 1 request\.\nRemaining after this: 2 today, 19/);
});

test('an already fetched date is never requested again', () => {
  const c = refreshControl(granted, { fetched: true, state: 'cached' });
  assert.equal(c.enabled, false);
  assert.match(c.reason, /already fetched/);
});

test('a refused or failed attempt is not a fetch: refresh stays possible with an allowance', () => {
  assert.equal(refreshControl(granted, { fetched: false, state: 'provider_error' }).enabled, true);
  assert.equal(refreshControl(exhausted, { fetched: false, state: 'quota_exhausted' }).enabled, false);
});

test('a running refresh disables the control (no double click)', () => {
  const c = refreshControl(granted, { fetched: false }, { busy: true });
  assert.equal(c.enabled, false);
  assert.equal(c.label, 'Refreshing…');
});

test('cached data is labelled as a snapshot with its update time, never as live', () => {
  const today = dataBadge({ state: 'cached', period: 'today', last_updated_at: '2026-10-23T09:00:00Z' }, NOW);
  assert.equal(today.kind, 'cached');
  assert.match(today.text, /^Cached snapshot · not live · updated 3 h ago \(/);
  assert.match(dataBadge({ state: 'cached', period: 'past', last_updated_at: '2026-10-20T09:00:00Z' }, NOW).text,
    /^Cached results snapshot · updated 3 days ago/);
  assert.equal(dataBadge({ state: 'not_cached' }).kind, 'none');
  assert.equal(dataBadge({ state: 'cached', period: 'today', last_updated_at: null }).text,
    'Cached snapshot · not live · update time unknown');
  assert.equal(formatUpdated(null), 'never updated');
});

test('legacy allowance, when any is left, is shown separately', () => {
  assert.match(allowanceText({ ...granted, legacy: { cap: 5, used: 3, remaining: 2 } }), /^2 legacy · /);
});

test('refresh outcomes produce a readable message for every status', () => {
  assert.deepEqual(refreshOutcome(200, { cache: { cached_fixtures: 12 } }), { ok: true, text: 'Fixtures updated: 12.' });
  assert.equal(refreshOutcome(409, { detail: { message: 'x is already cached; no request sent.' } }).text,
    'x is already cached; no request sent.');
  assert.equal(refreshOutcome(403, { detail: {} }).ok, false);
  assert.match(refreshOutcome(502, { detail: { message: 'Refresh did not return fixtures: HTTP Error 429' } }).text, /429/);
  assert.match(refreshOutcome(500, null).text, /cached fixtures are unchanged/);
});

test('every fixture state has its own label', () => {
  assert.equal(dataBadge({ state: 'quota_exhausted' }).text, 'Not cached · no authorized request left');
  assert.equal(dataBadge({ state: 'provider_error', last_attempt: { error: 'HTTP 503' } }).text,
    'Not cached · last refresh failed (HTTP 503)');
  assert.match(dataBadge({ state: 'no_fixtures', last_updated_at: '2026-10-23T09:00:00Z' }, NOW).text,
    /^No fixtures scheduled · updated 3 h ago/);
  assert.equal(dataBadge({ state: 'fresh', period: 'today', last_updated_at: '2026-10-23T11:59:50Z' }, NOW).kind, 'cached');
  assert.equal(refreshOutcome(429, { detail: { message: 'quota' } }).ok, false);
  assert.equal(timesNote({ display_timezone: 'Europe/Istanbul' }), 'Times in Europe/Istanbul');
});

test('dates are explicit Europe/Istanbul days: the October 23 boundary', () => {
  const t = new Date('2026-10-22T20:30:36Z');             // Tehran midnight, the old automatic switch
  assert.equal(dateInZone(t), '2026-10-22');                // still 23:30 in Istanbul
  assert.equal(dateInZone(t, 'Asia/Tehran'), '2026-10-23');
  assert.equal(dateInZone(new Date('2026-10-22T20:59:59Z')), '2026-10-22');
  assert.equal(dateInZone(new Date('2026-10-22T21:00:00Z')), '2026-10-23');
});

test('demo mode never retries a failed load in the background', () => {
  assert.equal(maxLoadRetries(exhausted), 0);
  assert.equal(maxLoadRetries(null), 3);
});

test('football-data.org provider: its own label, daily count and refresh wording', () => {
  const fd = { mode: 'demo', provider: 'football_data', provider_label: 'football-data.org', cost_per_refresh: 1,
    can_refresh: true, remaining_today: 39, remaining_total: 39, daily_limit: 40, blocked_reasons: [] };
  assert.equal(allowanceText(fd), 'football-data.org · 39 of 40 requests left today');
  assert.match(confirmText(fd, '2026-10-31'), /^Request fixtures for 2026-10-31 from football-data\.org\?/);
  assert.match(refreshControl(fd, { fetched: true, provider: 'football_data' }).reason, /15 minutes/);
  assert.equal(refreshControl(fd, { fetched: false }).enabled, true);
});
