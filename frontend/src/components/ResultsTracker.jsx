import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, CheckCircle2, XCircle, HelpCircle, Trophy, TrendingUp, BarChart3, ChevronDown, ChevronUp, Calendar, Target, ArrowLeft, AlertTriangle, ShieldCheck, Shield } from 'lucide-react';

const API = "http://127.0.0.1:8000/api";

const TIER_STYLES = {
  1: { accent: "text-yellow-400", bg: "bg-yellow-500/10", border: "border-yellow-500/30", badge: "bg-yellow-500/15 text-yellow-400", barFill: "from-yellow-500 to-yellow-400", label: "🏆 Tier 1" },
  2: { accent: "text-emerald-400", bg: "bg-emerald-500/8", border: "border-emerald-500/25", badge: "bg-emerald-500/15 text-emerald-400", barFill: "from-emerald-500 to-emerald-400", label: "🥈 Tier 2" },
  3: { accent: "text-cyan-400", bg: "bg-cyan-500/6", border: "border-cyan-500/20", badge: "bg-cyan-500/10 text-cyan-400", barFill: "from-cyan-500 to-cyan-400", label: "🥉 Tier 3" },
  4: { accent: "text-blue-400", bg: "bg-blue-500/5", border: "border-blue-500/15", badge: "bg-blue-500/10 text-blue-400", barFill: "from-blue-500 to-blue-400", label: "Tier 4" },
  5: { accent: "text-violet-400", bg: "bg-violet-500/5", border: "border-violet-500/15", badge: "bg-violet-500/10 text-violet-400", barFill: "from-violet-500 to-violet-400", label: "Tier 5" },
  6: { accent: "text-slate-400", bg: "bg-slate-500/5", border: "border-slate-500/15", badge: "bg-slate-500/10 text-slate-400", barFill: "from-slate-500 to-slate-400", label: "Tier 6" },
};

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

const ResultBadge = ({ result, isSettled }) => {
  if (!isSettled) {
    return (
      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-slate-800/50 text-slate-600 border border-slate-700/30 opacity-40">
        <HelpCircle className="w-3 h-3" /> N/A
      </span>
    );
  }
  if (result === true) {
    return (
      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-emerald-500/15 text-emerald-400 border border-emerald-500/25">
        <CheckCircle2 className="w-3 h-3" /> ✓
      </span>
    );
  }
  if (result === false) {
    return (
      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider bg-red-500/15 text-red-400 border border-red-500/25">
        <XCircle className="w-3 h-3" /> ✗
      </span>
    );
  }
  return null;
};

/* ── Tier Accuracy Bar (for global summary) ──────────── */
const TierAccuracyRow = ({ tier }) => {
  const style = TIER_STYLES[tier.tier] || TIER_STYLES[6];
  const acc = tier.accuracy || 0;

  return (
    <div className={`flex items-center gap-3 px-4 py-2.5 rounded-lg border ${style.border} ${style.bg}`}>
      <Shield className={`w-4 h-4 ${style.accent} shrink-0`} />
      <span className={`text-xs font-bold ${style.accent} w-16 shrink-0`}>{style.label}</span>
      <div className="flex-1 h-2 bg-black/30 rounded-full overflow-hidden">
        <div className={`h-full bg-gradient-to-r ${style.barFill} rounded-full transition-all duration-700`} style={{ width: `${acc}%` }} />
      </div>
      <span className={`text-sm font-mono font-black w-14 text-right ${getAccuracyColor(acc)}`}>{acc}%</span>
      <span className="text-[9px] text-slate-600 font-mono w-16 text-right">{tier.correct}/{tier.settled}</span>
    </div>
  );
};

/* ── Match Result Tier Card ──────────── */
const TierResultCard = ({ tier }) => {
  const style = TIER_STYLES[tier.tier] || TIER_STYLES[6];
  const { summary, picks } = tier;
  const settled = picks.filter(p => p.isSettled);
  const unsettled = picks.filter(p => !p.isSettled);

  return (
    <div className={`border ${style.border} rounded-lg overflow-hidden`}>
      {/* Tier header */}
      <div className={`flex items-center justify-between px-3 py-2 ${style.bg} border-b border-white/5`}>
        <div className="flex items-center gap-2">
          <Shield className={`w-3.5 h-3.5 ${style.accent}`} />
          <span className={`text-[11px] font-bold ${style.accent} uppercase tracking-wider`}>{style.label}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-mono font-bold ${getAccuracyColor(summary.accuracy)}`}>
            {summary.correct}/{summary.settled}
          </span>
          <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded ${getAccuracyBg(summary.accuracy)}`}>
            {summary.accuracy}%
          </span>
        </div>
      </div>

      {/* Picks */}
      <div className="divide-y divide-white/[0.03]">
        {settled.map((pick, idx) => (
          <div key={idx} className={`flex items-center justify-between px-3 py-2 ${pick.result === true ? 'bg-emerald-500/[0.03]' : pick.result === false ? 'bg-red-500/[0.03]' : ''}`}>
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className={`text-[9px] ${style.accent} font-bold uppercase w-14 shrink-0`}>{pick.section}</span>
              <span className="text-[12px] text-slate-300 truncate">{pick.market}</span>
            </div>
            <div className="flex items-center gap-2.5 shrink-0">
              <span className="text-[10px] font-mono text-slate-500 w-12 text-right">{pick.probability?.toFixed(1)}%</span>
              <ResultBadge result={pick.result} isSettled={true} />
            </div>
          </div>
        ))}
        {unsettled.map((pick, idx) => (
          <div key={`na-${idx}`} className="flex items-center justify-between px-3 py-1.5 opacity-25">
            <span className="text-[11px] text-slate-600 truncate">{pick.market}</span>
            <ResultBadge result={null} isSettled={false} />
          </div>
        ))}
      </div>
    </div>
  );
};

/* ── Match Result Card ──────────── */
const MatchResultCard = ({ match }) => {
  const [expanded, setExpanded] = useState(false);
  const { fixture, actual, tiers, summary } = match;

  const settledTotal = summary.total;
  const accuracy = settledTotal > 0 ? Math.round((summary.correct / settledTotal) * 100) : 0;

  return (
    <div className={`bg-surface-2 border rounded-xl overflow-hidden transition-all duration-300 animate-slide-in ${expanded ? 'border-white/15' : 'border-border hover:border-white/10'}`}>
      {/* Match Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left px-5 py-4 flex items-center gap-4 transition-colors hover:bg-white/[0.02]"
      >
        <img src={fixture.league.logo} alt="" className="w-5 h-5 object-contain shrink-0 opacity-70" />
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

        {/* Match Accuracy Badge */}
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ${getAccuracyBg(accuracy)} shrink-0`}>
          <span className={`text-xs font-mono font-black ${getAccuracyColor(accuracy)}`}>{summary.correct}/{settledTotal}</span>
          <span className="text-[9px] text-slate-500 uppercase">hits</span>
        </div>

        <div className="text-slate-500 shrink-0">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </button>

      {/* Expanded: Tier-by-Tier Results */}
      {expanded && (
        <div className="border-t border-border px-5 py-4 animate-fade-in">
          {/* Actual Stats Bar */}
          <div className="flex items-center gap-4 mb-4 text-[11px] text-slate-500">
            <span>⚽ Goals: <span className="text-white font-bold">{actual.total_goals}</span></span>
            {actual.fh_home_goals != null && actual.fh_away_goals != null && (
              <span>🕐 HT: <span className="text-slate-300 font-bold">{actual.fh_home_goals}-{actual.fh_away_goals}</span></span>
            )}
            {actual.total_corners != null && (
              <span>⛳ Corners: <span className="text-blue-400 font-bold">{actual.total_corners}</span></span>
            )}
            {actual.yellow_cards != null && (
              <span>🟨 Cards: <span className="text-yellow-400 font-bold">{actual.yellow_cards}</span></span>
            )}
            {actual.red_cards != null && actual.red_cards > 0 && (
              <span>🟥 Red: <span className="text-red-400 font-bold">{actual.red_cards}</span></span>
            )}
          </div>

          {/* Tiers */}
          <div className="space-y-3">
            {(tiers || []).map((tier) => (
              <TierResultCard key={tier.tier} tier={tier} />
            ))}
          </div>

          {/* Unsettled picks warning */}
          {(() => {
            const totalUnsettled = (tiers || []).reduce((sum, t) => sum + (t.summary?.unsettled || 0), 0);
            return totalUnsettled > 0 ? (
              <div className="mt-3 flex items-center gap-2 text-[10px] text-slate-600">
                <AlertTriangle className="w-3 h-3 text-amber-600" />
                <span>{totalUnsettled} pick(s) excluded from stats (missing data)</span>
              </div>
            ) : null;
          })()}
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
  const tierSummary = data?.tier_summary || [];
  const leagueQuality = data?.league_quality || {};

  const getOverallColor = (pct) => {
    if (pct >= 75) return "text-emerald-400";
    if (pct >= 60) return "text-green-400";
    if (pct >= 45) return "text-yellow-400";
    return "text-red-400";
  };

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
            <p className="text-slate-600 text-[10px] mt-2">Evaluating 6 tiers × 6 picks per match</p>
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
            {/* ── Overall Summary Card ──────────────── */}
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
                  <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-wider">Settled</p>
                </div>
              </div>

              {/* Overall Accuracy Bar */}
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
                <span>{summary.total_matches} matches × 36 picks</span>
                <span>
                  <span className="text-emerald-500">■</span> Correct
                  <span className="text-red-500 ml-2">■</span> Wrong
                  {summary.na_excluded > 0 && (
                    <span className="text-slate-700 ml-2">| {summary.na_excluded} excluded</span>
                  )}
                </span>
              </div>

              {/* League exclusion info */}
              {excludedLeagueNames.length > 0 && (
                <div className="mt-3 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2 flex items-start gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" />
                  <div className="text-[10px] text-amber-400/70">
                    <span className="font-bold">{excludedLeagueNames.length} league(s) excluded</span> (&gt;25% unresolved):
                    {' '}{excludedLeagueNames.join(', ')}
                  </div>
                </div>
              )}
            </div>

            {/* ── Per-Tier Accuracy Summary ──────────── */}
            {tierSummary.length > 0 && (
              <div className="bg-surface-2 border border-white/10 rounded-2xl p-5 mb-6">
                <div className="flex items-center gap-2 mb-4">
                  <Shield className="w-4 h-4 text-yellow-400" />
                  <h3 className="text-xs font-bold tracking-[0.15em] text-white uppercase">Accuracy by Tier</h3>
                  <span className="text-[9px] text-slate-500 ml-auto">6 tiers × all matches</span>
                </div>
                <div className="space-y-2">
                  {tierSummary.map((tier) => (
                    <TierAccuracyRow key={tier.tier} tier={tier} />
                  ))}
                </div>
              </div>
            )}

            {/* ── Per-Match Results ──────────────────── */}
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
