import React, { useState, useEffect, useCallback } from 'react';
import {
  Loader2, CheckCircle2, XCircle, Trophy, TrendingUp,
  BarChart3, ChevronDown, ChevronUp, Calendar, Target, ArrowLeft,
  AlertTriangle, ShieldCheck
} from 'lucide-react';

const API = import.meta.env.VITE_API_URL || "/api";

/* ─── ResultBadge ───────────────────────────────────────── */
const ResultBadge = ({ result, isSettled }) => {
  if (!isSettled) return <span className="px-1.5 py-0.5 rounded text-[9px] font-bold text-slate-600 bg-slate-800/40 border border-slate-700/30">N/A</span>;
  if (result === true) return <span className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold text-emerald-400 bg-emerald-500/15 border border-emerald-500/30"><CheckCircle2 className="w-2.5 h-2.5" />WIN</span>;
  if (result === false) return <span className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold text-red-400 bg-red-500/15 border border-red-500/30"><XCircle className="w-2.5 h-2.5" />LOSS</span>;
  return null;
};

const accColor = (a) => a >= 85 ? 'text-emerald-400' : a >= 70 ? 'text-green-400' : a >= 55 ? 'text-yellow-400' : a >= 40 ? 'text-orange-400' : 'text-red-400';
const accBgBorder = (a) => a >= 85 ? 'bg-emerald-500/10 border-emerald-500/25' : a >= 70 ? 'bg-green-500/10 border-green-500/25' : a >= 55 ? 'bg-yellow-500/10 border-yellow-500/25' : a >= 40 ? 'bg-orange-500/10 border-orange-500/25' : 'bg-red-500/10 border-red-500/25';

/* ─── Match Result Card ─────────────────────────────────── */
const TennisMatchResultCard = ({ match }) => {
  const [expanded, setExpanded] = useState(false);
  const { fixture, result, picks = [], summary } = match || {};
  
  const settled = summary?.total || 0;
  const accuracy = settled > 0 ? Math.round(((summary?.correct || 0) / settled) * 100) : 0;
  
  const p1 = fixture?.player_1;
  const p2 = fixture?.player_2;
  const sets1 = result?.sets_1 ?? fixture?.sets_1;
  const sets2 = result?.sets_2 ?? fixture?.sets_2;
  const p1Won = sets1 > sets2;
  const p2Won = sets2 > sets1;

  return (
    <div className={`bg-surface-2 border rounded-xl overflow-hidden transition-all duration-300 ${expanded ? 'border-white/15' : 'border-border hover:border-white/10'}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left px-5 py-4 flex items-center gap-4 transition-colors hover:bg-white/[0.02]"
      >
        <div className="grid grid-cols-[minmax(0,1fr)_80px_minmax(0,1fr)] items-center gap-3 flex-1 min-w-0">
          <div className={`text-sm truncate text-right ${p1Won ? 'text-white font-bold' : 'text-slate-300 font-medium'}`}>
            {p1} {fixture?.rank_1 ? <span className="text-[10px] text-slate-500">#{fixture.rank_1}</span> : ''}
          </div>
          <div className="h-10 rounded-lg bg-black/30 border border-white/10 flex items-center justify-center tabular-nums font-mono text-lg font-black text-white">
            {sets1} - {sets2}
          </div>
          <div className={`text-sm truncate text-left ${p2Won ? 'text-white font-bold' : 'text-slate-300 font-medium'}`}>
            {p2} {fixture?.rank_2 ? <span className="text-[10px] text-slate-500">#{fixture.rank_2}</span> : ''}
          </div>
        </div>

        {/* Match hit badge */}
        <div className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border ${accBgBorder(accuracy)} shrink-0`}>
          <span className={`text-xs font-mono font-black ${accColor(accuracy)}`}>{summary?.correct || 0}/{settled}</span>
          <span className="text-[9px] text-slate-500 uppercase">hits</span>
        </div>

        <div className="text-slate-500 shrink-0">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </button>

      {/* Expanded: Picks Breakdown */}
      {expanded && (
        <div className="border-t border-border px-5 py-4">
          <div className="flex items-center gap-4 mb-4 text-[11px] text-slate-500 flex-wrap">
            <span>Tournament: <span className="text-white font-bold">{fixture?.tournament}</span></span>
            <span>Surface: <span className="text-emerald-400 font-bold uppercase">{fixture?.surface}</span></span>
            <span>Winner: <span className="text-white font-bold">{result?.winner || "TBD"}</span></span>
          </div>

          <div className="space-y-4">
            <div className="bg-[#111318] border border-white/5 rounded-xl overflow-hidden shadow-lg">
              <div className="px-4 py-3 bg-white/5 border-b border-white/5 flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wider text-slate-300">Match Predictions</span>
              </div>
              <div className="divide-y divide-white/[0.03]">
                {picks.length === 0 && (
                  <div className="p-4 text-[11px] text-slate-600 uppercase tracking-widest">No predictions for this match</div>
                )}
                {picks.map((pick, idx) => {
                  const isWin = pick.result === true;
                  const isLoss = pick.result === false;
                  const isSettled = pick.isSettled;

                  return (
                    <div
                      key={idx}
                      className={`flex flex-col sm:flex-row sm:items-center justify-between p-3 px-4 gap-3 hover:bg-white/[0.01] transition-colors ${
                        isSettled ? (isWin ? 'bg-emerald-500/[0.02]' : isLoss ? 'bg-red-500/[0.02]' : '') : 'opacity-65'
                      }`}
                    >
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <span className={`text-[13px] font-medium truncate ${isSettled ? 'text-slate-200' : 'text-slate-400'}`}>
                          {pick.market} — {pick.selection}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 shrink-0 sm:w-64 justify-between sm:justify-end">
                        <span className="text-[11px] font-mono font-bold text-emerald-400 w-10 text-right">
                          {pick.probability?.toFixed(1)}%
                        </span>
                        <div className="w-16 flex justify-end shrink-0">
                          <ResultBadge result={pick.result} isSettled={isSettled} />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/* ─── Main TennisResultsTracker ───────────────────────────────── */
const TennisResultsTracker = ({ onBack, selectedDate }) => {
  const [date, setDate] = useState(selectedDate || new Date().toISOString().slice(0, 10));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchResults = useCallback(async (dateStr, isBackground = false) => {
    if (!isBackground) {
      setLoading(true);
      setError(null);
    }
    try {
      const res = await fetch(`${API}/tennis/results?date=${dateStr}&t=${Date.now()}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "Failed to fetch tennis results");
      }
      setData(await res.json());
    } catch (e) {
      if (!isBackground) setError(e.message);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, []);

  useEffect(() => { fetchResults(date); }, [date, fetchResults]);

  const summary = data?.summary || {};
  const matches = data?.matches || [];
  
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="shrink-0 bg-surface-1 border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <button
              onClick={onBack}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-white hover:border-white/20 transition-all text-xs"
            >
              <ArrowLeft className="w-3.5 h-3.5" />Back
            </button>
            <div className="flex items-center gap-2">
              <Target className="w-5 h-5 text-emerald-500" />
              <h2 className="text-base font-bold tracking-widest text-white uppercase">
                Tennis Results <span className="text-emerald-500">Tracker</span>
              </h2>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Calendar className="w-4 h-4 text-slate-500 ml-2" />
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500/50 transition-colors cursor-pointer"
            />
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20">
            <Loader2 className="w-10 h-10 animate-spin text-emerald-500 mb-4" />
            <p className="text-emerald-400/60 text-xs tracking-[0.2em] uppercase animate-pulse">Fetching Tennis Results…</p>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-8 text-center max-w-md">
              <XCircle className="w-10 h-10 mx-auto mb-3 text-red-500/60" />
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          </div>
        ) : matches.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-500">
            <Trophy className="w-12 h-12 mb-4 opacity-20" />
            <p className="text-sm">No settled tennis results available for this date.</p>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto">
            <div className="bg-surface-2 border border-emerald-500/20 rounded-2xl p-6 mb-6 shadow-lg shadow-emerald-500/5">
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-emerald-400" />
                  <h3 className="text-xs font-bold tracking-[0.15em] text-emerald-400 uppercase">Overall Tennis Accuracy</h3>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-4 mb-5">
                <div className="text-center">
                  <p className={`text-4xl font-mono font-black ${accColor(summary?.accuracy_pct || 0)}`}>{summary?.accuracy_pct || 0}%</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Accuracy</p>
                </div>
                <div className="text-center">
                  <p className="text-4xl font-mono font-black text-emerald-400">{summary?.total_correct || 0}</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Correct</p>
                </div>
                <div className="text-center">
                  <p className="text-4xl font-mono font-black text-red-400">{summary?.total_wrong || 0}</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Wrong</p>
                </div>
                <div className="text-center">
                  <p className="text-4xl font-mono font-black text-slate-400">{summary?.total_picks || 0}</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Settled</p>
                </div>
              </div>
              <div className="w-full h-3 bg-black/40 rounded-full overflow-hidden border border-white/5">
                <div className="h-full flex">
                  <div className="bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-1000" style={{ width: `${(summary?.total_picks || 0) > 0 ? ((summary?.total_correct || 0) / summary.total_picks * 100) : 0}%` }} />
                  <div className="bg-gradient-to-r from-red-500 to-red-400 transition-all duration-1000" style={{ width: `${(summary?.total_picks || 0) > 0 ? ((summary?.total_wrong || 0) / summary.total_picks * 100) : 0}%` }} />
                </div>
              </div>
            </div>
            
            <div className="flex items-center gap-2 mb-4">
              <TrendingUp className="w-4 h-4 text-slate-400" />
              <h3 className="text-xs font-bold tracking-[0.15em] text-slate-300 uppercase">Match-by-Match Breakdown</h3>
              <span className="text-[9px] text-slate-600 ml-auto">{matches.length} matches</span>
            </div>
            <div className="space-y-3">
              {matches.map((match, idx) => (
                <TennisMatchResultCard key={idx} match={match} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default TennisResultsTracker;
