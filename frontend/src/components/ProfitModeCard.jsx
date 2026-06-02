import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '../utils/apiClient';
import { isUpcoming } from '../utils/matchStatus';

export default function ProfitModeCard({ fixture, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);

  const canEvaluate = fixture && isUpcoming(fixture) && !fixture.is_stale;

  useEffect(() => {
    if (!canEvaluate || !open) {
      setData(null);
      return undefined;
    }
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const home = encodeURIComponent(fixture.home_team?.name || '');
        const away = encodeURIComponent(fixture.away_team?.name || '');
        const league = encodeURIComponent(fixture.league?.name || '');
        const q = new URLSearchParams({
          match_status: fixture.status || 'NS',
          is_stale: fixture.is_stale ? 'true' : 'false',
        });
        const res = await apiFetch(
          `/execution/profit-mode/${home}/${away}/${league}?${q}`,
        );
        if (!cancelled) setData(res);
      } catch {
        if (!cancelled) setData({ message: 'Profit Mode unavailable' });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [fixture?.id, fixture?.status, fixture?.is_stale, canEvaluate, open]);

  if (!fixture) return null;

  const approved = data?.approved_bets || data?.profit_mode?.approved_bets || [];
  const message = data?.message || data?.profit_mode?.message || '';
  const top = approved[0];

  return (
    <section className="border-b border-white/5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-white/[0.02]"
      >
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Profit Mode</span>
        {top && (
          <span className="text-[10px] font-mono text-emerald-400">
            {top.verdict || 'BET'}
          </span>
        )}
      </button>
      {open && (
        <div className="px-4 pb-4">
          {!canEvaluate ? (
            <p className="text-xs text-slate-500 leading-relaxed">
              {fixture.is_stale
                ? 'Status is stale — not evaluated.'
                : 'No current bet opportunity for this match.'}
            </p>
          ) : loading ? (
            <div className="flex items-center gap-2 text-slate-500 text-xs py-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Evaluating…
            </div>
          ) : (
            <div className="space-y-2">
              <p className={`text-sm leading-snug ${approved.length ? 'text-emerald-400/90' : 'text-slate-400'}`}>
                {approved.length
                  ? `${approved.length} approved pick${approved.length > 1 ? 's' : ''}`
                  : (message || 'No bet today — waiting for a clear edge.')}
              </p>
              {top && (
                <p className="text-xs text-slate-300">
                  {top.market}
                  {top.calibrated_probability != null && (
                    <span className="text-slate-500 ml-2">
                      {Number(top.calibrated_probability).toFixed(0)}%
                    </span>
                  )}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
