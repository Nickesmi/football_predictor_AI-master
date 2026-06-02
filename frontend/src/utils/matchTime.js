const LIVE = new Set(['LIVE', '1H', '2H', 'HT', 'ET', 'BT', 'P']);
const FT = new Set(['FT', 'AET', 'PEN']);
const NOT_LIVE_INFERRED = new Set([
    'LIVE_PENDING_PROVIDER',
    'LIVE_STATUS_PENDING',
    'LIKELY_LIVE_OR_HT',
    'RESULT_PENDING',
]);

function parseKickoff(match) {
    const raw = match ? .kickoff_iso || match ? .kickoff;
    if (raw) {
        if (typeof raw === 'number') return new Date(raw * 1000);
        const s = String(raw);
        if (/^\d+$/.test(s)) return new Date(parseInt(s, 10) * 1000);
        const d = new Date(s.includes('T') ? s : s.replace(' ', 'T'));
        if (!Number.isNaN(d.getTime())) return d;
    }
    if (match ? .date && match ? .time && /^\d{1,2}:\d{2}$/.test(String(match.time))) {
        const d = new Date(`${match.date}T${match.time}:00`);
        if (!Number.isNaN(d.getTime())) return d;
    }
    return null;
}

export function elapsedMinutes(match, now = new Date()) {
    if (match ? .elapsed != null && match.elapsed !== '') {
        const n = parseInt(match.elapsed, 10);
        if (!Number.isNaN(n) && n >= 0 && n <= 130) return n;
    }
    const kick = parseKickoff(match);
    if (!kick) return null;
    const mins = Math.floor((now - kick) / 60000);
    return mins > 0 && mins <= 130 ? mins : null;
}

/** Live clock label — updates every second via `now`. */
export function formatMatchClock(match, now = new Date()) {
    if (!match) return '—';
    const st = (match.status || 'NS').toUpperCase();
    if (FT.has(st) || match.is_finished) return 'FT';
    if (st === 'RESULT_PENDING') return '—';
    if (st === 'LIVE_PENDING_PROVIDER' || st === 'LIVE_STATUS_PENDING') return '—';
    if (st === 'LIKELY_LIVE_OR_HT') return '—';
    if (st === 'HT') return 'HT';
    if (LIVE.has(st) || st === 'LIVE') {
        const detail = match.status_detail || match.status_short;
        if (detail && /[\d']/.test(String(detail))) return String(detail).replace(/\s+/g, '');
        const mins = elapsedMinutes(match, now);
        if (mins != null) return `${mins}'`;
        return 'LIVE';
    }
    const kick = parseKickoff(match);
    if (kick) {
        const diffMs = kick - now;
        if (diffMs > 0) {
            const m = Math.ceil(diffMs / 60000);
            if (m < 60) return `in ${m}m`;
            const h = Math.floor(m / 60);
            const r = m % 60;
            return r > 0 ? `in ${h}h ${r}m` : `in ${h}h`;
        }
        // Kickoff passed — show elapsed minutes if the match should have started
        if (diffMs <= 0 && diffMs > -130 * 60000) {
            const mins = Math.floor(-diffMs / 60000);
            if (mins > 0) return `${mins}'`;
        }
    }
    return match.time || 'TBD';
}

export function isMatchLive(match) {
    if (match ? .is_live === false) return false;
    if (match ? .is_finished || match ? .is_result_pending) return false;
    if (match ? .is_live === true) return true;
    const s = (match ? .status || '').toUpperCase();
    if (NOT_LIVE_INFERRED.has(s)) return false;
    return LIVE.has(s) || s === 'LIVE';
}

export function isMatchFinished(match) {
    if (match ? .is_finished) return true;
    return FT.has((match ? .status || '').toUpperCase());
}

export function hasLiveScore(match) {
    return match ? .home_goals != null && match ? .away_goals != null;
}