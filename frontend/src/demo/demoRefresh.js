// Demo-mode decisions for the interface (no React, no network): what the Refresh control shows,
// whether background refreshes may run, and how cached data is labelled. The backend enforces the
// quota (src/ops/demo_quota.py); these rules only keep the interface from asking for requests.

export const isDemo = (status) => Boolean(status && status.mode === 'demo');

// In demo mode nothing refreshes by itself: no refetch on tab focus, no polling.
export const allowBackgroundRefresh = (status) => !isDemo(status);

const pad = (n) => String(n).padStart(2, '0');

export const formatUpdated = (iso, nowMs = Date.now()) => {
  if (!iso) return 'never updated';
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return 'update time unknown';
  const mins = Math.max(0, Math.round((nowMs - t.getTime()) / 60000));
  const ago = mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago` : mins < 48 * 60
    ? `${Math.round(mins / 60)} h ago` : `${Math.round(mins / 1440)} days ago`;
  const stamp = `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
  return `updated ${ago} (${stamp})`;
};

// One label per fixture state (api/demo_mode.py); cached data is never presented as live.
export const STATE_TEXT = {
  cached: 'Cached snapshot',
  fresh: 'Just retrieved',
  no_fixtures: 'No fixtures scheduled',
  quota_exhausted: 'Not cached · no authorized request left',
  provider_error: 'Not cached · last refresh failed',
  not_cached: 'Not cached yet',
};

export const dataBadge = (cache, nowMs = Date.now()) => {
  if (!cache) return null;
  const state = cache.state || 'not_cached';
  if (state === 'cached' || state === 'fresh') {
    const label = cache.period === 'past' ? 'Cached results snapshot' : 'Cached snapshot · not live';
    const when = cache.last_updated_at ? formatUpdated(cache.last_updated_at, nowMs) : 'update time unknown';
    return { kind: 'cached', state, text: `${label} · ${when}` };
  }
  if (state === 'no_fixtures') {
    return { kind: 'empty', state, text: `${STATE_TEXT.no_fixtures} · ${formatUpdated(cache.last_updated_at, nowMs)}` };
  }
  const error = state === 'provider_error' && cache.last_attempt?.error ? ` (${cache.last_attempt.error})` : '';
  return { kind: state === 'not_cached' ? 'none' : 'problem', state, text: `${STATE_TEXT[state] || state}${error}` };
};

export const timesNote = (cache) => (cache && cache.display_timezone ? `Times in ${cache.display_timezone}` : '');

// The fixture provider the demo refreshes from (api/demo_mode.py): API-Football, or football-data.org.
export const providerLabel = (status) => (status && status.provider_label) || 'API-Football';
const isFootballData = (status) => Boolean(status && status.provider === 'football_data');

export const allowanceText = (status) => {
  if (!isDemo(status)) return '';
  if (isFootballData(status)) {
    return `${providerLabel(status)} · ${status.remaining_today} of ${status.daily_limit} requests left today`;
  }
  const legacy = status.legacy && status.legacy.remaining > 0 ? `${status.legacy.remaining} legacy · ` : '';
  return `${legacy}${status.remaining_today} left today · ${status.remaining_total} left in authorized allowance`;
};

export const refreshControl = (status, cache, { busy = false } = {}) => {
  if (!isDemo(status)) return { visible: false, enabled: false };
  const cost = status.cost_per_refresh || 1;
  const base = { visible: true, cost, label: `Refresh fixtures (${cost} request)`, detail: allowanceText(status) };
  if (busy) return { ...base, enabled: false, label: 'Refreshing…', reason: 'A refresh is running.' };
  if (cache && cache.fetched) {
    const reason = isFootballData(status) && cache.provider === 'football_data'
      ? 'Cached. A day can be refreshed again 15 minutes after its last update, until the day is over.'
      : 'This date was already fetched; it is never requested again.';
    return { ...base, enabled: false, reason };
  }
  if (!status.can_refresh) {
    const why = (status.blocked_reasons && status.blocked_reasons[0]) || 'No authorized request remains.';
    return { ...base, enabled: false, reason: `Refresh unavailable: ${why}. Cached fixtures stay visible.` };
  }
  return { ...base, enabled: true, reason: '' };
};

export const confirmText = (status, date) => {
  const cost = status.cost_per_refresh || 1;
  if (isFootballData(status)) {
    return `Request fixtures for ${date} from ${providerLabel(status)}?\n\n`
      + `Cost: ${cost} request.\nRemaining after this: ${Math.max(0, status.remaining_today - cost)} today.`;
  }
  return `Request fixtures for ${date} from API-Football?\n\n`
    + `Cost: ${cost} request.\nRemaining after this: ${Math.max(0, status.remaining_today - cost)} today, `
    + `${Math.max(0, status.remaining_total - cost)} in the authorized allowance.`;
};

// The message to show after POST /api/demo/refresh-fixtures (FastAPI puts errors under `detail`).
export const refreshOutcome = (httpStatus, body) => {
  const detail = (body && (body.detail || body)) || {};
  if (httpStatus === 200) return { ok: true, text: detail.message || `Fixtures updated: ${detail.cache?.cached_fixtures ?? 0}.` };
  if (httpStatus === 409) return { ok: false, text: detail.message || 'Already cached or already running.' };
  if (httpStatus === 403 || httpStatus === 429) {
    return { ok: false, text: detail.message || 'No authorized request remains; nothing was sent.' };
  }
  return { ok: false, text: detail.message || 'Refresh failed; cached fixtures are unchanged.' };
};

// Calendar day of an instant in an explicit IANA time zone (never the browser's implicit zone).
export const DEFAULT_DISPLAY_TZ = 'Europe/Istanbul';

export const dateInZone = (instant, timeZone = DEFAULT_DISPLAY_TZ) => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(instant);
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
};

// Demo mode: one try per load (a failed load is shown, not retried in the background).
export const maxLoadRetries = (status) => (isDemo(status) ? 0 : 3);
