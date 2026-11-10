import React, { useEffect, useState } from 'react';
import { CONFIDENCE_STYLE } from '../demo/predictionInputs';

// Bankroll-qualified picks across the day's matches (GET /api/best-bets/{date}). Low data-confidence matches
// are left out unless the toggle is on (remembered per browser).
const STORE_KEY = 'bestBets.includeLow';
const readToggle = () => {
  try { return window.localStorage.getItem(STORE_KEY) === '1'; } catch { return false; }
};

export default function BestBets({ api, date, fixturesCount, onSelect }) {
  const [includeLow, setIncludeLow] = useState(readToggle);
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!date || !fixturesCount) return undefined;
    let stop = false;
    let timer = null;
    const load = (attempt) => {
      fetch(`${api}/best-bets/${date}?include_low=${includeLow ? 'true' : 'false'}`)
        .then((r) => r.json())
        .then((d) => {
          if (stop) return;
          setData(d);
          if (d.analyses_pending > 0 && attempt < 6) timer = setTimeout(() => load(attempt + 1), 10000);
        })
        .catch(() => {});
    };
    load(0);
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [api, date, includeLow, fixturesCount]);

  const toggle = () => {
    const v = !includeLow;
    setIncludeLow(v);
    try { window.localStorage.setItem(STORE_KEY, v ? '1' : '0'); } catch { /* storage unavailable */ }
  };

  const picks = (data && data.picks) || [];
  return (
    <div className="border-b border-white/5">
      <div className="px-4 py-2 flex items-center gap-2">
        <button type="button" onClick={() => setOpen(!open)} className="text-[10px] text-slate-400 uppercase tracking-widest font-bold">
          {open ? '▾' : '▸'} Best bets
        </button>
        <span className="text-[10px] font-bold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full">{data ? data.total : '…'}</span>
        <label className="ml-auto flex items-center gap-1.5 text-[10px] text-slate-500 cursor-pointer">
          <input type="checkbox" checked={includeLow} onChange={toggle} data-testid="include-low" />
          Include low confidence
        </label>
      </div>
      {open && (
        <div className="px-3 pb-2 space-y-1">
          {data && data.analyses_pending > 0 && (
            <div className="text-[10px] text-slate-500 px-1">{data.analyses_pending} matches still being analysed…</div>
          )}
          {data && !includeLow && data.excluded_low_confidence_matches > 0 && (
            <div className="text-[10px] text-slate-500 px-1">{data.excluded_low_confidence_matches} low-confidence matches left out.</div>
          )}
          {picks.length === 0 && <div className="text-[11px] text-slate-500 px-1">No qualified picks.</div>}
          {picks.map((p) => (
            <button type="button" key={`${p.fixture_id}-${p.market}`} onClick={() => onSelect && onSelect(p.fixture_id)}
              className="w-full text-left bg-black/20 hover:bg-black/40 rounded-lg px-2.5 py-1.5">
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-slate-200 truncate flex-1">{p.home} v {p.away}</span>
                <span className={`text-[9px] px-1.5 rounded border ${CONFIDENCE_STYLE[p.confidence] || ''}`}>{p.confidence}</span>
              </div>
              <div className="flex items-center gap-2 text-[10px] text-slate-400">
                <span className="truncate flex-1">{p.market}</span>
                <span className="font-mono text-emerald-300">{Number(p.probability).toFixed(1)}%</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
