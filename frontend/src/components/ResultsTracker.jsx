import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, CheckCircle2, XCircle, HelpCircle, Trophy, TrendingUp, BarChart3, ChevronDown, ChevronUp, Calendar, Target, ArrowLeft, AlertTriangle, ShieldCheck } from 'lucide-react';

const API = "http://127.0.0.1:8001/api";

const getBarColor = (pct) => {
  if (pct >= 75) return "from-emerald-500 to-emerald-400";
  if (pct >= 60) return "from-green-500 to-green-400";
  if (pct >= 45) return "from-yellow-500 to-yellow-400";
  if (pct >= 30) return "from-orange-500 to-orange-400";
  return "from-red-500 to-red-400";
};

const ResultBadge = ({ result, isSettled }) => {
  if (!isSettled) {
    return (
      <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-slate-800/50 text-slate-600 border border-slate-700/30 line-through opacity-40">
        <HelpCircle className="w-3.5 h-3.5" />
        Excluded
      </span>
    );
  }
  if (result === true) {
    return (
      <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-500/15 text-emerald-400 border border-emerald-500/25">
        <CheckCircle2 className="w-3.5 h-3.5" />
        Correct
      </span>
    );
  }
  if (result === false) {
    return (
      <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-red-500/15 text-red-400 border border-red-500/25">
        <XCircle className="w-3.5 h-3.5" />
        Wrong
      </span>
    );
  }
  return null;
};

const MatchResultCard = ({ match }) => {
  const [expanded, setExpanded] = useState(false);
  const { fixture, actual, picks, summary } = match;

  // Use only settled picks for accuracy
  const settledTotal = summary.total;
  const accuracy = settledTotal > 0 ? Math.round((summary.correct / settledTotal) * 100) : 0;

  const getAccuracyColor = (acc) => {
    if (acc >= 80) return "text-emerald-400";
    if (acc >= 60) return "text-green-400";
    if (acc >= 40) return "text-yellow-400";
    if (acc >= 20) return "text-orange-400";
    return "text-red-400";
  };

  const getAccuracyBg = (acc) => {
    if (acc >= 80) return "bg-emerald-500/10 border-emerald-500/25";
    if (acc >= 60) return "bg-green-500/10 border-green-500/25";
    if (acc >= 40) return "bg-yellow-500/10 border-yellow-500/25";
    if (acc >= 20) return "bg-orange-500/10 border-orange-500/25";
    return "bg-red-500/10 border-red-500/25";
  };

  // Separate settled and unsettled picks
  const settledPicks = picks.filter(p => p.isSettled !== false && p.result !== null && p.result !== undefined);
  const unsettledPicks = picks.filter(p => p.isSettled === false || p.result === null || p.result === undefined);

  return (
    <div className={`bg-surface-2 border rounded-xl overflow-hidden transition-all duration-300 animate-slide-in ${expanded ? 'border-white/15' : 'border-border hover:border-white/10'}`}>
      {/* Match Header - Clickable */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left px-5 py-4 flex items-center gap-4 transition-colors hover:bg-white/[0.02]"
      >
        {/* League Logo */}
        <img src={fixture.league.logo} alt="" className="w-5 h-5 object-contain shrink-0 opacity-70" />

        {/* Teams */}
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <img src={fixture.home_team.logo} alt="" className="w-5 h-5 object-contain shrink-0" />
            <span className="text-sm font-medium text-white truncate">{fixture.home_team.name}</span>
          </div>
          <span className="text-lg font-mono font-black text-white shrink-0">
            {actual.home_goals} - {actual.away_goals}
          </span>
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-medium text-white truncate">{fixture.away_team.name}</span>
            <img src={fixture.away_team.logo} alt="" className="w-5 h-5 object-contain shrink-0" />
          </div>
        </div>

        {/* Match Accuracy Badge — settled only */}
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ${getAccuracyBg(accuracy)} shrink-0`}>
          <span className={`text-xs font-mono font-black ${getAccuracyColor(accuracy)}`}>{summary.correct}/{settledTotal}</span>
          <span className="text-[9px] text-slate-500 uppercase">hits</span>
        </div>

        {/* Expand Arrow */}
        <div className="text-slate-500 shrink-0">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </button>

      {/* Expanded Picks Detail */}
      {expanded && (
        <div className="border-t border-border px-5 py-4 animate-fade-in">
          {/* Actual Stats Bar */}
          <div className="flex items-center gap-4 mb-4 text-[11px] text-slate-500">
            <span>⚽ Goals: <span className="text-white font-bold">{actual.total_goals}</span></span>
            {actual.total_corners !== null && actual.total_corners !== undefined && (
              <span>⛳ Corners: <span className="text-blue-400 font-bold">{actual.total_corners}</span></span>
            )}
            {actual.yellow_cards !== null && actual.yellow_cards !== undefined && (
              <span>🟨 Yellow Cards: <span className="text-yellow-400 font-bold">{actual.yellow_cards}</span></span>
            )}
            {actual.red_cards !== null && actual.red_cards !== undefined && actual.red_cards > 0 && (
              <span>🟥 Red Cards: <span className="text-red-400 font-bold">{actual.red_cards}</span></span>
            )}
            {actual.total_cards !== null && actual.total_cards !== undefined && (
              <span className="text-slate-600">| Total Cards: <span className="text-slate-400 font-bold">{actual.total_cards}</span></span>
            )}
          </div>

          {/* Settled Picks Table */}
          <div className="overflow-hidden rounded-lg border border-white/5">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-surface-1 border-b border-white/5">
                  <th className="py-2.5 px-4 text-[10px] font-semibold text-slate-500 tracking-wider">#</th>
                  <th className="py-2.5 px-4 text-[10px] font-semibold text-slate-500 tracking-wider">PREDICTED MARKET</th>
                  <th className="py-2.5 px-4 text-[10px] font-semibold text-slate-500 tracking-wider text-right">CONFIDENCE</th>
                  <th className="py-2.5 px-4 text-[10px] font-semibold text-slate-500 tracking-wider text-right">RESULT</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {/* Settled picks first */}
                {settledPicks.map((pick, idx) => (
                  <tr key={idx} className={`transition-colors ${pick.result === true ? 'bg-emerald-500/[0.04]' :
                      pick.result === false ? 'bg-red-500/[0.04]' :
                        ''
                    }`}>
                    <td className="py-2.5 px-4 text-[11px] text-slate-600 font-mono">
                      {String(idx + 1).padStart(2, '0')}
                    </td>
                    <td className="py-2.5 px-4 text-[13px] font-medium text-white">
                      {pick.market}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <div className="w-16 h-1.5 bg-black/40 rounded-full overflow-hidden border border-white/5">
                          <div
                            className={`h-full bg-gradient-to-r ${getBarColor(pick.probability)} rounded-full`}
                            style={{ width: `${Math.min(pick.probability, 100)}%` }}
                          />
                        </div>
                        <span className="text-[11px] font-mono font-bold text-slate-400 w-12 text-right">{pick.probability.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      <ResultBadge result={pick.result} isSettled={true} />
                    </td>
                  </tr>
                ))}
                {/* Unsettled picks — dimmed */}
                {unsettledPicks.map((pick, idx) => (
                  <tr key={`na-${idx}`} className="opacity-25">
                    <td className="py-2 px-4 text-[11px] text-slate-700 font-mono">
                      —
                    </td>
                    <td className="py-2 px-4 text-[12px] text-slate-600 line-through">
                      {pick.market}
                    </td>
                    <td className="py-2 px-4 text-right">
                      <span className="text-[10px] text-slate-700">N/A</span>
                    </td>
                    <td className="py-2 px-4 text-right">
                      <ResultBadge result={null} isSettled={false} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Settlement info */}
          {unsettledPicks.length > 0 && (
            <div className="mt-3 flex items-center gap-2 text-[10px] text-slate-600">
              <AlertTriangle className="w-3 h-3 text-amber-600" />
              <span>{unsettledPicks.length} pick(s) excluded from stats (missing data)</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};


const ResultsTracker = ({ onBack, selectedDate }) => {
  const [date, setDate] = useState(selectedDate || new Date().toISOString().slice(0, 10));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchResults = useCallback(async (dateStr) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/results/${dateStr}`);
      if (!res.ok) throw new Error("Failed to fetch results");
      const json = await res.json();
      setData(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchResults(date);
  }, [date, fetchResults]);

  const summary = data?.summary || {};
  const matches = data?.matches || [];
  const leagueQuality = data?.league_quality || {};

  const getOverallColor = (pct) => {
    if (pct >= 75) return "text-emerald-400";
    if (pct >= 60) return "text-green-400";
    if (pct >= 45) return "text-yellow-400";
    return "text-red-400";
  };

  // Count excluded leagues
  const excludedLeagueNames = Object.entries(leagueQuality)
    .filter(([_, q]) => q.excluded)
    .map(([name]) => name);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 bg-surface-1 border-b border-border px-6 py-4">
        <div className="flex items-center gap-4 mb-4">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-white hover:border-white/20 transition-all text-xs"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back
          </button>
          <div className="flex items-center gap-2">
            <Target className="w-5 h-5 text-amber-500" />
            <h2 className="text-base font-bold tracking-widest text-white uppercase">
              Results <span className="text-amber-500">Tracker</span>
            </h2>
          </div>
        </div>

        {/* Date Selector */}
        <div className="flex items-center gap-3">
          <Calendar className="w-4 h-4 text-slate-500" />
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/50 transition-colors"
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20">
            <Loader2 className="w-10 h-10 animate-spin text-amber-500 mb-4" />
            <p className="text-amber-400/60 text-xs tracking-[0.2em] uppercase animate-pulse">Verifying Predictions…</p>
            <p className="text-slate-600 text-[10px] mt-2">Fetching match results & statistics</p>
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
            <p className="text-sm">No valid settled results available for this date.</p>
            <p className="text-xs text-slate-600 mt-1">Try selecting a past date with completed matches.</p>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto">
            {/* Overall Summary Card */}
            <div className="bg-surface-2 border border-amber-500/20 rounded-2xl p-6 mb-6 shadow-lg shadow-amber-500/5">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-amber-400" />
                  <h3 className="text-xs font-bold tracking-[0.15em] text-amber-400 uppercase">Overall Accuracy</h3>
                </div>
                <div className="flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
                  <span className="text-[9px] text-emerald-500/80 font-semibold uppercase tracking-wider">Clean Stats</span>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="text-center">
                  <p className={`text-3xl font-mono font-black ${getOverallColor(summary.accuracy_pct)}`}>
                    {summary.accuracy_pct}%
                  </p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Accuracy</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-mono font-black text-emerald-400">{summary.total_correct}</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Correct</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-mono font-black text-red-400">{summary.total_wrong}</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Wrong</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-mono font-black text-slate-400">{summary.total_picks}</p>
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Settled Picks</p>
                </div>
              </div>

              {/* Accuracy Bar — correct + wrong = 100% */}
              <div className="w-full h-3 bg-black/40 rounded-full overflow-hidden border border-white/5">
                <div className="h-full flex">
                  <div
                    className="bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-1000"
                    style={{ width: `${summary.total_picks > 0 ? (summary.total_correct / summary.total_picks * 100) : 0}%` }}
                  />
                  <div
                    className="bg-gradient-to-r from-red-500 to-red-400 transition-all duration-1000"
                    style={{ width: `${summary.total_picks > 0 ? (summary.total_wrong / summary.total_picks * 100) : 0}%` }}
                  />
                </div>
              </div>
              <div className="flex justify-between mt-2 text-[9px] text-slate-600">
                <span>{summary.total_matches} matches analyzed</span>
                <span>
                  <span className="text-emerald-500">■</span> Correct
                  <span className="text-red-500 ml-2">■</span> Wrong
                  {summary.na_excluded > 0 && (
                    <span className="text-slate-700 ml-2">| {summary.na_excluded} picks excluded (missing data)</span>
                  )}
                </span>
              </div>

              {/* League exclusion info */}
              {excludedLeagueNames.length > 0 && (
                <div className="mt-3 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2 flex items-start gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" />
                  <div className="text-[10px] text-amber-400/70">
                    <span className="font-bold">{excludedLeagueNames.length} league(s) excluded</span> (>25% unresolved):
                    {' '}{excludedLeagueNames.join(', ')}
                  </div>
                </div>
              )}
            </div>

            {/* Per-Match Results */}
            <div className="flex items-center gap-2 mb-4">
              <TrendingUp className="w-4 h-4 text-slate-400" />
              <h3 className="text-xs font-bold tracking-[0.15em] text-slate-300 uppercase">Match-by-Match Breakdown</h3>
            </div>

            <div className="space-y-3">
              {matches.map((match, idx) => (
                <MatchResultCard key={idx} match={match} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ResultsTracker;
