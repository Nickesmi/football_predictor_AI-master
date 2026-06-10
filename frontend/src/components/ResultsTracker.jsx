import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Loader2, CheckCircle2, XCircle, HelpCircle, Trophy, TrendingUp,
  BarChart3, ChevronDown, ChevronUp, Calendar, Target, ArrowLeft,
  AlertTriangle, ShieldCheck, Shield, Layers, Activity, Zap, Crown,
  Database, RefreshCw, Cpu
} from 'lucide-react';

const API = import.meta.env.VITE_API_URL || "/api";

/* ─── Error Boundary ─────────────────────────────────────── */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, errorInfo) {
    console.error("ResultsTracker Error Boundary caught an error:", error, errorInfo);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="p-6 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 max-w-2xl mx-auto my-10 font-sans">
          <h2 className="text-lg font-bold mb-2 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-red-500" />
            Results Tracker Render Error
          </h2>
          <p className="text-sm mb-4 text-slate-350">
            A runtime error occurred while rendering the results interface. Please see details below:
          </p>
          <pre className="text-xs bg-black/50 p-4 rounded overflow-auto font-mono text-red-300 border border-red-500/20 max-h-96">
            {this.state.error?.toString()}
            {"\n\n"}
            {this.state.error?.stack}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ─── Category style map ─────────────────────────────────── */
const CATEGORY_STYLES = {
  "Result":     { accent:'text-emerald-400', bg:'bg-emerald-500/10', border:'border-emerald-500/25', bar:'from-emerald-500 to-emerald-300', label:'Result' },
  "Goals":      { accent:'text-cyan-400',    bg:'bg-cyan-500/8',     border:'border-cyan-500/20',    bar:'from-cyan-500 to-cyan-300',       label:'Total Goals' },
  "Team Goals": { accent:'text-teal-400',    bg:'bg-teal-500/8',     border:'border-teal-500/20',    bar:'from-teal-500 to-teal-300',       label:'Team Goals' },
  "Handicaps":  { accent:'text-fuchsia-400', bg:'bg-fuchsia-500/5',  border:'border-fuchsia-500/15', bar:'from-fuchsia-500 to-fuchsia-300', label:'Handicaps' },
};

/* ─── Helpers ───────────────────────────────────────────── */
const accColor  = (a) => a >= 85 ? 'text-emerald-400' : a >= 70 ? 'text-green-400' : a >= 55 ? 'text-yellow-400' : a >= 40 ? 'text-orange-400' : 'text-red-400';
const accBgBorder = (a) => a >= 85 ? 'bg-emerald-500/10 border-emerald-500/25' : a >= 70 ? 'bg-green-500/10 border-green-500/25' : a >= 55 ? 'bg-yellow-500/10 border-yellow-500/25' : a >= 40 ? 'bg-orange-500/10 border-orange-500/25' : 'bg-red-500/10 border-red-500/25';

const categoryGrade = (a) => {
  const val = a || 0;
  if (val >= 90) return { label: 'S', color: 'text-yellow-300 bg-yellow-500/20 border-yellow-500/40' };
  if (val >= 80) return { label: 'A', color: 'text-emerald-300 bg-emerald-500/20 border-emerald-500/40' };
  if (val >= 70) return { label: 'B', color: 'text-blue-300 bg-blue-500/20 border-blue-500/40' };
  if (val >= 55) return { label: 'C', color: 'text-yellow-400 bg-yellow-500/15 border-yellow-500/30' };
  if (val >= 40) return { label: 'D', color: 'text-orange-400 bg-orange-500/15 border-orange-500/30' };
  return { label: 'F', color: 'text-red-400 bg-red-500/15 border-red-500/30' };
};

/* ─── ResultBadge ───────────────────────────────────────── */
const ResultBadge = ({ result, isSettled }) => {
  if (!isSettled)  return <span className="px-1.5 py-0.5 rounded text-[9px] font-bold text-slate-600 bg-slate-800/40 border border-slate-700/30">N/A</span>;
  if (result === true)  return <span className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold text-emerald-400 bg-emerald-500/15 border border-emerald-500/30"><CheckCircle2 className="w-2.5 h-2.5" />WIN</span>;
  if (result === false) return <span className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold text-red-400 bg-red-500/15 border border-red-500/30"><XCircle className="w-2.5 h-2.5" />LOSS</span>;
  return null;
};

/* ─── Tier Accuracy Card (Layer 2 summary) ─────────── */
const TIER_STYLES = {
  "tier1": { accent:'text-emerald-400', bg:'bg-emerald-500/10', border:'border-emerald-500/25', bar:'from-emerald-500 to-emerald-300', label:'Tier 1 · Top Ranked Group' },
  "tier2": { accent:'text-blue-400',    bg:'bg-blue-500/8',     border:'border-blue-500/20',    bar:'from-blue-500 to-blue-300',       label:'Tier 2 · Second Ranked Group' },
  "tier3": { accent:'text-amber-400',   bg:'bg-amber-500/8',    border:'border-amber-500/20',   bar:'from-amber-500 to-amber-300',     label:'Tier 3 · Third Ranked Group' },
};

const TierAccuracyCard = ({ tierData }) => {
  const st = TIER_STYLES[tierData?.id] || TIER_STYLES.tier3;
  const acc = tierData?.accuracy || 0;
  const grade = categoryGrade(acc);

  return (
    <div className={`relative flex flex-col gap-2 p-4 rounded-xl border ${st.border} ${st.bg} overflow-hidden group transition-all duration-300 hover:scale-[1.02] hover:shadow-lg`}>
      {/* Glow orb */}
      <div className={`absolute -top-4 -right-4 w-16 h-16 rounded-full bg-gradient-to-br ${st.bar} opacity-10 blur-xl group-hover:opacity-20 transition-opacity`} />

      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <ShieldCheck className={`w-3.5 h-3.5 ${st.accent}`} />
          <span className={`text-[11px] font-black uppercase tracking-wider ${st.accent}`}>{st.label}</span>
        </div>
        <span className={`text-[11px] font-black px-2 py-0.5 rounded border ${grade.color}`}>{grade.label}</span>
      </div>

      {/* Big accuracy number */}
      <div className="text-center py-1">
        <span className={`text-3xl font-mono font-black ${accColor(acc)}`}>{acc.toFixed(1)}%</span>
      </div>

      {/* Progress bar */}
      <div className="h-2 bg-black/40 rounded-full overflow-hidden border border-white/5">
        <div
          className={`h-full bg-gradient-to-r ${st.bar} rounded-full transition-all duration-1000`}
          style={{ width: `${acc}%` }}
        />
      </div>

      {/* Correct / Wrong counts */}
      <div className="flex items-center justify-between text-[10px] font-mono">
        <span className="text-emerald-400 font-bold">✓ {tierData?.correct || 0}</span>
        <span className="text-slate-500">{(tierData?.settled || 0)} settled</span>
        <span className="text-red-400 font-bold">✗ {tierData?.wrong || 0}</span>
      </div>
    </div>
  );
};

/* CategoryResultCard component removed in favor of a unified flat picks list */

/* ─── Match Result Card ─────────────────────────────────── */
const MatchResultCard = ({ match }) => {
  const [expanded, setExpanded] = useState(false);
  const { fixture, actual, categories = [], tiers = [], summary, picks = [] } = match || {};
  const settled  = summary?.total || 0;
  const accuracy = settled > 0 ? Math.round(((summary?.correct || 0) / settled) * 100) : 0;

  const sortedPicks = useMemo(() => {
    return [...picks].sort((a, b) => b.probability - a.probability);
  }, [picks]);

  const displayTiers = useMemo(() => {
    if (tiers && tiers.length) return tiers;
    const tierSize = Math.max(1, Math.ceil(sortedPicks.length / 3));
    return [
      { id: 'tier1', name: 'Tier 1', label: 'Top Ranked Group', picks: sortedPicks.slice(0, tierSize) },
      { id: 'tier2', name: 'Tier 2', label: 'Second Ranked Group', picks: sortedPicks.slice(tierSize, tierSize * 2) },
      { id: 'tier3', name: 'Tier 3', label: 'Third Ranked Group', picks: sortedPicks.slice(tierSize * 2) },
    ];
  }, [tiers, sortedPicks]);

  return (
    <div className={`bg-surface-2 border rounded-xl overflow-hidden transition-all duration-300 ${expanded ? 'border-white/15' : 'border-border hover:border-white/10'}`}>
      {/* Match Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left px-5 py-4 flex items-center gap-4 transition-colors hover:bg-white/[0.02]"
      >
        {fixture?.league?.logo && (
          <img src={fixture.league.logo} alt="" className="w-5 h-5 object-contain shrink-0 opacity-70" onError={e => e.target.style.display='none'} />
        )}
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            {fixture?.home_team?.logo && (
              <img src={fixture.home_team.logo} alt="" className="w-5 h-5 object-contain shrink-0" onError={e => e.target.style.display='none'} />
            )}
            <span className="text-sm font-medium text-white truncate">{fixture?.home_team?.name || "Home"}</span>
          </div>
          <span className="text-lg font-mono font-black text-white shrink-0">
            {actual?.home_goals ?? 0} – {actual?.away_goals ?? 0}
          </span>
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-medium text-white truncate">{fixture?.away_team?.name || "Away"}</span>
            {fixture?.away_team?.logo && (
              <img src={fixture.away_team.logo} alt="" className="w-5 h-5 object-contain shrink-0" onError={e => e.target.style.display='none'} />
            )}
          </div>
        </div>

        {/* Mini category accuracy row */}
        <div className="hidden md:flex items-center gap-2.5 shrink-0">
          {(categories || []).map(c => {
            if (!c || !CATEGORY_STYLES[c.category]) return null;
            const st = CATEGORY_STYLES[c.category];
            const a  = c.summary?.accuracy || 0;
            return (
              <div key={c.category} className="flex flex-col items-center gap-0.5">
                <span className={`text-[8px] font-bold ${st.accent}`}>{st.label.split(' ')[0]}</span>
                <div className={`w-1.5 h-6 rounded-full bg-black/40 overflow-hidden border ${st.border}`}>
                  <div
                    className={`w-full bg-gradient-to-t ${st.bar} rounded-full transition-all duration-700`}
                    style={{ height: `${a}%`, marginTop: `${100 - a}%` }}
                  />
                </div>
              </div>
            );
          })}
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

      {/* Expanded: Category-by-Category Breakdown */}
      {expanded && (
        <div className="border-t border-border px-5 py-4">
          {/* Actual Stats */}
          <div className="flex items-center gap-4 mb-4 text-[11px] text-slate-500 flex-wrap">
            <span>Outcome: <span className="text-white font-bold">
              {(actual?.home_goals ?? 0) > (actual?.away_goals ?? 0) ? "Home Win" : 
               (actual?.home_goals ?? 0) < (actual?.away_goals ?? 0) ? "Away Win" : "Draw"}
            </span></span>
            <span>Total Goals: <span className="text-white font-bold">{actual?.total_goals ?? 0}</span></span>
            <span>Home Goals: <span className="text-emerald-400 font-bold">{actual?.home_goals ?? 0}</span></span>
            <span>Away Goals: <span className="text-orange-400 font-bold">{actual?.away_goals ?? 0}</span></span>
            <span>Goal Diff: <span className="text-fuchsia-400 font-bold">
              {((actual?.home_goals ?? 0) - (actual?.away_goals ?? 0)) > 0 
                ? `+${(actual?.home_goals ?? 0) - (actual?.away_goals ?? 0)}` 
                : (actual?.home_goals ?? 0) - (actual?.away_goals ?? 0)}
            </span></span>
          </div>

          {/* Sorted Picks Chart */}
          <div className="space-y-4">
            {displayTiers.map((tier) => {
              const tierStyle = TIER_STYLES[tier.id] || TIER_STYLES.tier3;
              const tierPicks = tier.picks || [];
              const settledTier = tierPicks.filter(p => p.isSettled);
              const tierCorrect = settledTier.filter(p => p.result === true).length;
              const tierAccuracy = settledTier.length ? (tierCorrect / settledTier.length) * 100 : 0;

              return (
                <div key={tier.id} className={`bg-[#111318] border ${tierStyle.border} rounded-xl overflow-hidden shadow-lg`}>
                  {/* Tier Header */}
                  <div className={`px-4 py-3 ${tierStyle.bg} border-b border-white/5 flex items-center justify-between`}>
                    <div className="flex items-center gap-2">
                      <Crown className={`w-4 h-4 ${tierStyle.accent}`} />
                      <div>
                        <span className={`text-xs font-bold uppercase tracking-wider block ${tierStyle.accent}`}>{tier.name}</span>
                        <span className={`text-[9px] ${tierStyle.accent} opacity-70 uppercase tracking-widest`}>{tier.label}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`bg-black/25 ${accColor(tierAccuracy)} border border-white/5 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider font-mono`}>
                        {tierAccuracy.toFixed(1)}% acc
                      </span>
                      <span className={`bg-white/10 ${tierStyle.accent} text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider font-mono`}>
                        {tierPicks.length} picks
                      </span>
                    </div>
                  </div>

                  {/* Tier List */}
                  <div className="divide-y divide-white/[0.03]">
                    {tierPicks.length === 0 && (
                      <div className="p-4 text-[11px] text-slate-600 uppercase tracking-widest">No picks in this rank group</div>
                    )}
                    {tierPicks.map((pick, idx) => {
                      const st = CATEGORY_STYLES[pick.section] || { border: "border-white/5", accent: "text-slate-400", bg: "bg-white/5", bar: "from-slate-500 to-slate-400", label: pick.section, emoji: "🛡️" };
                      const isWin = pick.result === true;
                      const isLoss = pick.result === false;
                      const isSettled = pick.isSettled;

                      const getIcon = (sec) => {
                        switch (sec) {
                          case "Result": return <Trophy className="w-3.5 h-3.5 text-emerald-400" />;
                          case "Goals": return <Target className="w-3.5 h-3.5 text-cyan-400" />;
                          case "Team Goals": return <TrendingUp className="w-3.5 h-3.5 text-teal-400" />;
                          case "Handicaps": return <Zap className="w-3.5 h-3.5 text-fuchsia-400" />;
                          default: return <Shield className="w-3.5 h-3.5 text-slate-400" />;
                        }
                      };

                      const getBadgeStyle = (sec) => {
                        switch (sec) {
                          case "Result": return "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20";
                          case "Goals": return "bg-cyan-500/15 text-cyan-400 border border-cyan-500/20";
                          case "Team Goals": return "bg-teal-500/15 text-teal-400 border border-teal-500/20";
                          case "Handicaps": return "bg-fuchsia-500/15 text-fuchsia-400 border border-fuchsia-500/20";
                          default: return "bg-slate-500/15 text-slate-400 border border-white/5";
                        }
                      };

                      return (
                        <div
                          key={idx}
                          className={`flex flex-col sm:flex-row sm:items-center justify-between p-3 px-4 gap-3 hover:bg-white/[0.01] transition-colors ${
                            isSettled ? (isWin ? 'bg-emerald-500/[0.02]' : isLoss ? 'bg-red-500/[0.02]' : '') : 'opacity-65'
                          }`}
                        >
                          {/* Left: Category badge & Market description */}
                          <div className="flex items-center gap-3 flex-1 min-w-0">
                            <div className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold tracking-wider shrink-0 uppercase ${getBadgeStyle(pick.section)}`}>
                              {getIcon(pick.section)}
                              <span>{st.label}</span>
                            </div>
                            <span className={`text-[13px] font-medium truncate ${isSettled ? 'text-slate-200' : 'text-slate-400'}`}>
                              {pick.market}
                            </span>
                          </div>

                          {/* Right: Confidence/Accuracy bar & Result Badge */}
                          <div className="flex items-center gap-3 shrink-0 sm:w-64 justify-between sm:justify-end">
                            <div className="flex-1 h-1.5 bg-black/40 rounded-full overflow-hidden hidden sm:block border border-white/5">
                              <div
                                className={`h-full bg-gradient-to-r ${st.bar} rounded-full`}
                                style={{ width: `${Math.min(pick.probability, 100)}%` }}
                              />
                            </div>
                            <span className="text-[11px] font-mono font-bold text-slate-400 w-10 text-right">
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
              );
            })}
          </div>

          {/* Unsettled warning */}
          {(() => {
            const nu = sortedPicks.filter(p => !p.isSettled).length;
            return nu > 0 ? (
              <div className="mt-3 flex items-center gap-2 text-[10px] text-slate-600">
                <AlertTriangle className="w-3 h-3 text-amber-600" />
                <span>{nu} pick(s) excluded from stats (missing data)</span>
              </div>
            ) : null;
          })()}
        </div>
      )}
    </div>
  );
};

/* ─── Main ResultsTracker ───────────────────────────────── */
const ResultsTracker = ({ onBack, selectedDate }) => {
  const [date, setDate]     = useState(selectedDate || new Date().toISOString().slice(0, 10));
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true); // default to true to show loading indicator instantly
  const [error, setError]   = useState(null);
  const [retraining, setRetraining] = useState(false);
  const [retrainSummary, setRetrainSummary] = useState(null);

  const handleRetrain = async () => {
    if (!confirm("Are you sure you want to retrain the global isotonic calibration engine? This will modify the baseline probabilities for future predictions.")) return;
    
    setRetraining(true);
    setRetrainSummary(null);
    try {
      const res = await fetch(`${API}/admin/retrain-engine`, { method: 'POST' });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || "Failed to retrain engine");
      setRetrainSummary(json);
    } catch (e) {
      alert(`Error retraining engine: ${e.message}`);
    } finally {
      setRetraining(false);
    }
  };

  const fetchResults = useCallback(async (dateStr) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/results/${dateStr}?t=${Date.now()}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "Failed to fetch results");
      }
      setData(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchResults(date); }, [date, fetchResults]);

  const summary      = data?.summary      || {};
  const matches      = data?.matches      || [];
  const categorySummary = data?.category_summary || [];
  const leagueQuality = data?.league_quality || {};

  const excludedLeagues = Object.entries(leagueQuality || {})
    .filter(([, q]) => q && q.excluded)
    .map(([name]) => name);

  const overallColor = (p) => p >= 75 ? 'text-emerald-400' : p >= 60 ? 'text-green-400' : p >= 45 ? 'text-yellow-400' : 'text-red-400';

  /* active categories = those with at least 1 settled pick */
  const activeCategories = (categorySummary || []).filter(c => c && (c.settled || 0) > 0);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* ── Header ─────────────────────────────────────────── */}
      <div className="shrink-0 bg-surface-1 border-b border-border px-6 py-4">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-4">
            <button
              onClick={onBack}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-white hover:border-white/20 transition-all text-xs"
            >
              <ArrowLeft className="w-3.5 h-3.5" />Back
            </button>
            <div className="flex items-center gap-2">
              <Target className="w-5 h-5 text-amber-500" />
              <h2 className="text-base font-bold tracking-widest text-white uppercase">
                Results <span className="text-amber-500">Tracker</span>
              </h2>
              {matches.length > 0 && (
                <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 text-[10px] font-black px-2 py-0.5 rounded-full font-mono ml-1.5">
                  {matches.length} {matches.length === 1 ? 'Match' : 'Matches'}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleRetrain}
              disabled={retraining}
              className="flex items-center gap-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-50"
            >
              {retraining ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Cpu className="w-4 h-4" />}
              <span>Retrain Engine</span>
            </button>

            <Calendar className="w-4 h-4 text-slate-500 ml-2" />
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/50 transition-colors cursor-pointer"
            />
          </div>
        </div>

        {/* Retrain Summary Banner */}
        {retrainSummary && (
          <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-4 mb-4 text-sm relative overflow-hidden shadow-[0_0_15px_rgba(16,185,129,0.1)]">
            <div className="absolute -top-4 -right-4 w-24 h-24 bg-emerald-500/20 blur-2xl rounded-full" />
            <div className="flex items-start justify-between">
              <div className="flex gap-3">
                <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
                <div>
                  <h4 className="font-bold text-white mb-1">Engine Retraining Complete</h4>
                  <p className="text-slate-400 text-xs mb-3">Updated calibration models safely across {retrainSummary.models_fitted} markets.</p>
                  
                  <div className="flex flex-wrap gap-4">
                    {Object.entries(retrainSummary.calibration_details || {}).map(([market, details]) => (
                      details.fitted && (
                        <div key={market} className="bg-black/30 border border-white/5 rounded-lg p-3 w-48">
                          <div className="text-[10px] font-bold text-slate-300 uppercase tracking-wider mb-2">{market}</div>
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            <div className="text-slate-500 text-[9px] uppercase">Before</div>
                            <div className="text-emerald-500 text-[9px] uppercase text-right">After</div>
                            
                            <div className="col-span-2 text-[10px] text-slate-500 border-b border-white/5 pb-1">Calibration Gap</div>
                            <div className="text-slate-300 font-mono">{details.gap_before > 0 ? '+' : ''}{details.gap_before}%</div>
                            <div className="text-emerald-400 font-mono text-right">{details.gap_after > 0 ? '+' : ''}{details.gap_after}%</div>

                            <div className="col-span-2 text-[10px] text-slate-500 border-b border-white/5 pb-1 mt-1">Brier Score</div>
                            <div className="text-slate-300 font-mono">{details.old_brier?.toFixed(4) || '---'}</div>
                            <div className="text-emerald-400 font-mono text-right">{details.brier_score?.toFixed(4)}</div>
                          </div>
                        </div>
                      )
                    ))}
                  </div>
                </div>
              </div>
              <button onClick={() => setRetrainSummary(null)} className="text-slate-500 hover:text-white">
                <XCircle className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Content ────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20">
            <Loader2 className="w-10 h-10 animate-spin text-amber-500 mb-4" />
            <p className="text-amber-400/60 text-xs tracking-[0.2em] uppercase animate-pulse">Verifying Predictions…</p>
            <p className="text-slate-600 text-[10px] mt-2">Evaluating categories × picks across all finished matches</p>
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
            <p className="text-sm">No settled results available for this date.</p>
            <p className="text-xs text-slate-600 mt-1">Select a past date with completed matches.</p>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto">

            {/* ══ OVERALL ACCURACY BANNER ════════════════════ */}
            <div className="bg-surface-2 border border-amber-500/20 rounded-2xl p-6 mb-6 shadow-lg shadow-amber-500/5">
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-amber-400" />
                  <h3 className="text-xs font-bold tracking-[0.15em] text-amber-400 uppercase">Overall Accuracy</h3>
                </div>
                <div className="flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
                  <span className="text-[9px] text-emerald-500/80 font-semibold uppercase tracking-wider">Verified Results Data</span>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-4 mb-5">
                <div className="text-center">
                  <p className={`text-4xl font-mono font-black ${overallColor(summary?.accuracy_pct || 0)}`}>{summary?.accuracy_pct || 0}%</p>
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

              {/* Stacked bar */}
              <div className="w-full h-3 bg-black/40 rounded-full overflow-hidden border border-white/5">
                <div className="h-full flex">
                  <div
                    className="bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-1000"
                    style={{ width: `${(summary?.total_picks || 0) > 0 ? ((summary?.total_correct || 0) / summary.total_picks * 100) : 0}%` }}
                  />
                  <div
                    className="bg-gradient-to-r from-red-500 to-red-400 transition-all duration-1000"
                    style={{ width: `${(summary?.total_picks || 0) > 0 ? ((summary?.total_wrong || 0) / summary.total_picks * 100) : 0}%` }}
                  />
                </div>
              </div>
              <div className="flex justify-between mt-2 text-[9px] text-slate-600">
                <span>{summary?.total_matches || 0} matches evaluated</span>
                <span>
                  <span className="text-emerald-500">■</span> Correct
                  <span className="text-red-500 ml-2">■</span> Wrong
                  {(summary?.na_excluded || 0) > 0 && <span className="text-slate-700 ml-2">| {summary.na_excluded} excluded (N/A)</span>}
                </span>
              </div>

              {excludedLeagues.length > 0 && (
                <div className="mt-3 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2 flex items-start gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" />
                  <div className="text-[10px] text-amber-400/70">
                    <span className="font-bold">{excludedLeagues.length} league(s) excluded</span> (&gt;25% unresolved):
                    {' '}{excludedLeagues.join(', ')}
                  </div>
                </div>
              )}
            </div>

            {/* ══ LAYER 2 TIER ACCURACY DASHBOARD ═══════════ */}
            {data?.tier_summary && data.tier_summary.length > 0 && (
              <div className="bg-surface-2 border border-white/10 rounded-2xl p-6 mb-6">
                {/* Section header */}
                <div className="flex items-center justify-between mb-5">
                  <div className="flex items-center gap-2">
                    <Layers className="w-4 h-4 text-violet-400" />
                    <h3 className="text-xs font-bold tracking-[0.15em] text-white uppercase">Layer 2 — Tier Accuracy</h3>
                  </div>
                  <div className="flex items-center gap-1.5 text-[9px] text-slate-500">
                    <Activity className="w-3 h-3" />
                    <span>3 rank tiers · {(data?.tier_summary || []).reduce((s, t) => s + (t?.settled || 0), 0)} picks settled</span>
                  </div>
                </div>

                {/* ── Tier Performance Grid ────────────────────────── */}
                {data?.summary && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5">
                    {(data.tier_summary || []).slice(0, 3).map(t => (
                      <TierAccuracyCard key={t.tier} tierData={t} />
                    ))}
                  </div>
                )}

                {/* Compact horizontal bar view */}
                <div className="border-t border-white/5 pt-4">
                  <p className="text-[9px] text-slate-600 uppercase tracking-widest mb-3">Aligned Category Accuracy</p>
                  <div className="space-y-2">
                    {activeCategories.map(cat => {
                      if (!cat) return null;
                      const st  = CATEGORY_STYLES[cat.category] || CATEGORY_STYLES["Result"];
                      const acc = cat.accuracy || 0;
                      return (
                        <div key={cat.category} className="flex items-center gap-3">
                          <span className={`text-[10px] font-bold ${st.accent} w-20 shrink-0`}>{st.label}</span>
                          <div className="flex-1 h-2 bg-black/30 rounded-full overflow-hidden">
                            <div
                              className={`h-full bg-gradient-to-r ${st.bar} rounded-full transition-all duration-1000`}
                              style={{ width: `${acc}%` }}
                            />
                          </div>
                          <span className={`text-[11px] font-mono font-black w-14 text-right ${accColor(acc)}`}>{acc.toFixed(1)}%</span>
                          <span className="text-[9px] font-mono text-slate-600 w-14 text-right">{cat.correct}/{cat.settled}</span>
                          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${categoryGrade(acc).color} w-6 text-center`}>{categoryGrade(acc).label}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Best / Worst callout */}
                {activeCategories.length >= 2 && (() => {
                  const sorted = [...activeCategories].sort((a, b) => (b?.accuracy || 0) - (a?.accuracy || 0));
                  const best  = sorted[0];
                  const worst = sorted[sorted.length - 1];
                  if (!best || !worst) return null;

                  const bestStyle = CATEGORY_STYLES[best.category] || CATEGORY_STYLES["Result"];
                  const worstStyle = CATEGORY_STYLES[worst.category] || CATEGORY_STYLES["Result"];

                  return (
                    <div className="grid grid-cols-2 gap-3 mt-4 border-t border-white/5 pt-4">
                      <div className="flex items-center gap-3 p-3 rounded-xl bg-emerald-500/5 border border-emerald-500/15">
                        <Zap className="w-5 h-5 text-emerald-400 shrink-0" />
                        <div>
                          <p className="text-[9px] text-emerald-500/70 uppercase tracking-wider">Best Category</p>
                          <p className="text-sm font-black text-emerald-400">{bestStyle.label} · {(best.accuracy || 0).toFixed(1)}%</p>
                          <p className="text-[9px] text-slate-500">{best.correct} correct of {best.settled}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 p-3 rounded-xl bg-orange-500/5 border border-orange-500/15">
                        <AlertTriangle className="w-5 h-5 text-orange-400 shrink-0" />
                        <div>
                          <p className="text-[9px] text-orange-500/70 uppercase tracking-wider">Lowest Category</p>
                          <p className="text-sm font-black text-orange-400">{worstStyle.label} · {(worst.accuracy || 0).toFixed(1)}%</p>
                          <p className="text-[9px] text-slate-500">{worst.correct} correct of {worst.settled}</p>
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* ══ PER-MATCH BREAKDOWN ════════════════════════ */}
            <div className="flex items-center gap-2 mb-4">
              <TrendingUp className="w-4 h-4 text-slate-400" />
              <h3 className="text-xs font-bold tracking-[0.15em] text-slate-300 uppercase">Match-by-Match Breakdown</h3>
              <span className="text-[9px] text-slate-600 ml-auto">{matches.length} matches</span>
            </div>
            <div className="space-y-3">
              {matches.map((match, idx) => {
                if (!match) return null;
                return <MatchResultCard key={idx} match={match} />;
              })}
            </div>

          </div>
        )}
      </div>
    </div>
  );
};

/* ─── Export wrapped in Error Boundary ───────────────────── */
const ResultsTrackerWithErrorBoundary = (props) => (
  <ErrorBoundary>
    <ResultsTracker {...props} />
  </ErrorBoundary>
);

export default ResultsTrackerWithErrorBoundary;
