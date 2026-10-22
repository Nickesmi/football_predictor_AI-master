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

export const dataBadge = (cache, nowMs = Date.now()) => {
  if (!cache) return null;
  if (!cache.cached) return { kind: 'none', text: 'No cached fixtures for this date' };
  const label = cache.period === 'past' ? 'Cached results snapshot' : 'Cached snapshot · not live';
  const when = cache.last_updated_at ? formatUpdated(cache.last_updated_at, nowMs) : 'update time unknown';
  return { kind: 'cached', text: `${label} · ${when}` };
};

export const allowanceText = (status) => {
  if (!isDemo(status)) return '';
  const legacy = status.legacy && status.legacy.remaining > 0 ? `${status.legacy.remaining} legacy · ` : '';
  return `${legacy}${status.remaining_today} left today · ${status.remaining_total} left in authorized allowance`;
};

export const refreshControl = (status, cache, { busy = false } = {}) => {
  if (!isDemo(status)) return { visible: false, enabled: false };
  const cost = status.cost_per_refresh || 1;
  const base = { visible: true, cost, label: `Refresh fixtures (${cost} request)`, detail: allowanceText(status) };
  if (busy) return { ...base, enabled: false, label: 'Refreshing…', reason: 'A refresh is running.' };
  if (cache && cache.cached) {
    return { ...base, enabled: false, reason: 'This date is already cached; it is never requested again.' };
  }
  if (!status.can_refresh) {
    const why = (status.blocked_reasons && status.blocked_reasons[0]) || 'No authorized request remains.';
    return { ...base, enabled: false, reason: `Refresh unavailable: ${why}. Cached fixtures stay visible.` };
  }
  return { ...base, enabled: true, reason: '' };
};

export const confirmText = (status, date) => {
  const cost = status.cost_per_refresh || 1;
  return `Request fixtures for ${date} from API-Football?\n\n`
    + `Cost: ${cost} request.\nRemaining after this: ${Math.max(0, status.remaining_today - cost)} today, `
    + `${Math.max(0, status.remaining_total - cost)} in the authorized allowance.`;
};

// The message to show after POST /api/demo/refresh-fixtures (FastAPI puts errors under `detail`).
export const refreshOutcome = (httpStatus, body) => {
  const detail = (body && (body.detail || body)) || {};
  if (httpStatus === 200) return { ok: true, text: `Fixtures updated: ${detail.cache?.cached_fixtures ?? 0} cached.` };
  if (httpStatus === 409) return { ok: false, text: detail.message || 'Already cached or already running.' };
  if (httpStatus === 403) return { ok: false, text: detail.message || 'No authorized request remains.' };
  return { ok: false, text: detail.message || 'Refresh failed; cached fixtures are unchanged.' };
};
