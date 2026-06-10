import React, { useState, useMemo } from 'react';
import { Loader2, AlertCircle, ShieldAlert, Cpu, ChevronDown, ChevronRight, Activity, Zap, Target, CornerUpRight, CreditCard, Trophy, Layers, Filter, BarChart3, TrendingUp, Shuffle, Crosshair, Crown, Shield } from 'lucide-react';

const getBarColor = (pct) => {
  if (pct >= 95) return "bg-emerald-500";
  if (pct >= 90) return "bg-green-500";
  if (pct >= 85) return "bg-lime-500";
  if (pct >= 80) return "bg-yellow-500";
  if (pct >= 70) return "bg-orange-400";
  if (pct >= 60) return "bg-amber-600";
  return "bg-slate-600";
};

const getTextColor = (pct) => {
  if (pct >= 80) return "text-emerald-400";
  if (pct >= 70) return "text-orange-400";
  if (pct >= 60) return "text-amber-500";
  return "text-slate-500";
};

const getSectionIcon = (secName) => {
  switch (secName) {
    case "Goals": return <Target className="w-4 h-4 text-cyan-400" />;
    case "Team Goals": return <TrendingUp className="w-4 h-4 text-teal-400" />;
    case "First Half": return <Activity className="w-4 h-4 text-violet-400" />;
    case "Second Half": return <Activity className="w-4 h-4 text-orange-400" />;
    case "Corners": return <CornerUpRight className="w-4 h-4 text-blue-400" />;
    case "Handicaps": return <Zap className="w-4 h-4 text-fuchsia-400" />;
    case "Cards": return <CreditCard className="w-4 h-4 text-amber-400" />;
    case "Result": return <Trophy className="w-4 h-4 text-emerald-400" />;
    default: return <BarChart3 className="w-4 h-4 text-slate-400" />;
  }
};

const getSectionGradient = (secName) => {
  switch (secName) {
    case "Goals": return "from-cyan-500/10 to-transparent";
    case "Team Goals": return "from-teal-500/10 to-transparent";
    case "First Half": return "from-violet-500/10 to-transparent";
    case "Second Half": return "from-orange-500/10 to-transparent";
    case "Corners": return "from-blue-500/10 to-transparent";
    case "Handicaps": return "from-fuchsia-500/10 to-transparent";
    case "Cards": return "from-amber-500/10 to-transparent";
    case "Result": return "from-emerald-500/10 to-transparent";
    default: return "from-white/5 to-transparent";
  }
};

/* ── Category styling ──────────────────── */
const CATEGORY_STYLES = {
  "Result": { border: "border-emerald-500/30", glow: "shadow-[0_0_12px_rgba(16,185,129,0.06)]", accent: "text-emerald-400", bg: "from-emerald-500/10 to-transparent", badge: "bg-emerald-500/15 text-emerald-400", label: "Result Pick", desc: "Match outcome" },
  "Goals": { border: "border-cyan-500/25", glow: "shadow-[0_0_12px_rgba(6,182,212,0.06)]", accent: "text-cyan-400", bg: "from-cyan-500/8 to-transparent", badge: "bg-cyan-500/15 text-cyan-400", label: "Total Goals", desc: "Over/Under lines" },
  "Team Goals": { border: "border-teal-500/25", glow: "shadow-[0_0_12px_rgba(20,184,166,0.06)]", accent: "text-teal-400", bg: "from-teal-500/8 to-transparent", badge: "bg-teal-500/15 text-teal-400", label: "Team Goals", desc: "Individual team scores" },
  "Handicaps": { border: "border-fuchsia-500/25", glow: "shadow-[0_0_12px_rgba(217,70,239,0.06)]", accent: "text-fuchsia-400", bg: "from-fuchsia-500/8 to-transparent", badge: "bg-fuchsia-500/15 text-fuchsia-400", label: "Handicaps", desc: "Spread & handicap lines" },
};

/* ── Score Prediction Card ──────────────────── */
const ScorePrediction = ({ scorePrediction, dominance, homeName, awayName, poisson, actual, status }) => {
  if (!scorePrediction) return null;

  const ftScores = poisson?.top_scorelines || scorePrediction.full_time || [];
  const fhScores = scorePrediction.first_half || [];
  const allScorelines = poisson?.all_scorelines || [];
  const expectedGoals = scorePrediction.expected_goals || {};
  const corners = dominance?.corners || {};
  const cards = dominance?.cards || {};

  return (
    <div className="mb-8 space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <Crosshair className="w-5 h-5 text-violet-400" />
        <h3 className="text-sm font-bold tracking-[0.15em] text-white uppercase">Score Prediction</h3>
        <span className="bg-violet-500/10 text-violet-400 text-[9px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider">Poisson Model</span>
      </div>

      {/* xG Summary */}
      <div className="bg-[#111318] border border-white/5 rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="text-center">
            <span className="text-[9px] text-slate-500 uppercase tracking-wider block mb-1">xG Home</span>
            <span className="text-xl font-mono font-bold text-cyan-400">{expectedGoals.home}</span>
          </div>
          <div className="text-[10px] text-slate-600 font-mono">vs</div>
          <div className="text-center">
            <span className="text-[9px] text-slate-500 uppercase tracking-wider block mb-1">xG Away</span>
            <span className="text-xl font-mono font-bold text-orange-400">{expectedGoals.away}</span>
          </div>
        </div>
        <div className="text-center bg-white/5 px-4 py-2 rounded-lg">
          <span className="text-[9px] text-slate-500 uppercase tracking-wider block mb-1">Total xG</span>
          <span className="text-xl font-mono font-bold text-white">{expectedGoals.total}</span>
        </div>
      </div>

      {/* ── POST-MATCH ANALYSIS (Only for Finished Matches) ── */}
      {status === 'FT' && actual && (
        <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-5 mb-6 relative overflow-hidden">
          <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/20 blur-3xl rounded-full" />
          
          <div className="flex items-center gap-2 mb-4">
            <Trophy className="w-5 h-5 text-emerald-400" />
            <h3 className="text-sm font-bold tracking-[0.15em] text-white uppercase">Post-Match Learning</h3>
            <span className="bg-emerald-500/20 text-emerald-400 text-[9px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider ml-auto">Engine Updated</span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {/* Score Comparison */}
            <div className="bg-black/20 p-3 rounded-lg border border-white/5">
              <span className="text-[10px] text-slate-400 uppercase tracking-wider mb-2 block">Scoreline</span>
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs text-slate-500">Predicted:</span>
                <span className="text-sm font-mono text-slate-300">
                  {scorePrediction?.full_time?.[0]?.home ?? 0}-{scorePrediction?.full_time?.[0]?.away ?? 0}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-emerald-400 font-bold">Actual:</span>
                <span className="text-sm font-mono font-bold text-white">
                  {actual?.home_goals ?? 0}-{actual?.away_goals ?? 0}
                </span>
              </div>
            </div>

            {/* Result Comparison */}
            <div className="bg-black/20 p-3 rounded-lg border border-white/5">
              <span className="text-[10px] text-slate-400 uppercase tracking-wider mb-2 block">Result</span>
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs text-slate-500">Predicted:</span>
                <span className="text-xs text-slate-300">
                  {(poisson?.result?.home_win ?? 0) > (poisson?.result?.away_win ?? 0) ? "Home Win" : "Away Win"}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-emerald-400 font-bold">Actual:</span>
                <span className="text-xs font-bold text-white">
                  {(actual?.home_goals ?? 0) > (actual?.away_goals ?? 0) ? "Home Win" : 
                   (actual?.home_goals ?? 0) < (actual?.away_goals ?? 0) ? "Away Win" : "Draw"}
                </span>
              </div>
            </div>

            {/* Model Error */}
            <div className="bg-black/20 p-3 rounded-lg border border-white/5 md:col-span-2">
              <span className="text-[10px] text-slate-400 uppercase tracking-wider mb-2 block">Prediction Error (Goals)</span>
              <div className="flex items-center gap-4">
                <div className="flex-1">
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-500">Expected Total: <span className="text-white">{expectedGoals.total}</span></span>
                    <span className="text-emerald-400">Actual: <span className="text-white">{actual?.total_goals ?? 0}</span></span>
                  </div>
                  <div className="h-1.5 w-full bg-white/10 rounded-full overflow-hidden flex">
                    <div className="bg-slate-400 h-full" style={{ width: `${Math.min(((expectedGoals.total || 0) / 6) * 100, 100)}%` }} />
                    <div className="bg-emerald-400 h-full" style={{ width: `${Math.min((Math.abs((actual?.total_goals ?? 0) - (expectedGoals.total || 0)) / 6) * 100, 100)}%` }} />
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <span className="text-[10px] text-slate-500 block">Delta</span>
                  <span className={`text-sm font-bold font-mono ${(actual?.total_goals ?? 0) - (expectedGoals.total || 0) > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {((actual?.total_goals ?? 0) - (expectedGoals.total || 0)) > 0 ? '+' : ''}{((actual?.total_goals ?? 0) - (expectedGoals.total || 0)).toFixed(1)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* FT & FH Scores */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {[{ scores: ftScores, label: "Full Time", color: "cyan" }, { scores: fhScores, label: "First Half", color: "violet" }].map(({ scores, label, color }) => (
          <div key={label} className="bg-[#111318] border border-white/5 rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-white/5 flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full bg-${color}-400`} />
              <span className="text-xs font-bold text-white uppercase tracking-wider">{label}</span>
              <span className="text-[9px] text-slate-500 ml-auto">Top {scores.length}</span>
            </div>
            <div className="divide-y divide-white/[0.03]">
              {scores.map((score, idx) => {
                const isActual = status === 'FT' && label === 'Full Time' && 
                                 score.score === `${actual?.home_goals ?? -1}-${actual?.away_goals ?? -1}`;
                
                // Keep compatibility with old structure
                const homeS = score.home !== undefined ? score.home : (score.score ? score.score.split('-')[0] : 0);
                const awayS = score.away !== undefined ? score.away : (score.score ? score.score.split('-')[1] : 0);
                
                return (
                  <div key={idx} className={`flex items-center justify-between px-4 py-2.5 ${idx === 0 && !isActual ? `bg-${color}-500/[0.04]` : ''} ${isActual ? 'bg-emerald-500/20 border-l-2 border-emerald-400' : ''}`}>
                    <div className="flex items-center gap-3">
                      {idx === 0 && !isActual && <Crown className="w-3.5 h-3.5 text-yellow-400" />}
                      {isActual && <Trophy className="w-3.5 h-3.5 text-emerald-400" />}
                      <span className={`text-lg font-mono font-bold ${idx === 0 || isActual ? 'text-white' : 'text-slate-400'}`}>
                        {homeS} - {awayS}
                      </span>
                      {isActual && <span className="text-[9px] font-bold text-emerald-400 uppercase tracking-wider px-1.5 py-0.5 bg-emerald-500/20 rounded">Actual</span>}
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-black/40 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full bg-${color}-400 animate-grow`} style={{ width: `${Math.min(score.probability * 3, 100)}%`, opacity: idx === 0 || isActual ? 1 : 0.5 }} />
                      </div>
                      <span className={`text-sm font-mono font-bold min-w-[44px] text-right ${idx === 0 || isActual ? `text-${color}-400` : 'text-slate-500'}`}>{score.probability}%</span>
                    </div>
                  </div>
                );
              })}
              
              {/* Actual Result Out of Top 5 */}
              {status === 'FT' && label === 'Full Time' && actual && allScorelines.length > 0 && (() => {
                const actualStr = `${actual.home_goals}-${actual.away_goals}`;
                const inTop5 = scores.some(s => (s.score || `${s.home}-${s.away}`) === actualStr);
                
                if (inTop5) return null;
                
                const actualScoreData = allScorelines.find(s => s.score === actualStr) || { rank: '>99', probability: 0.0 };
                const rank = actualScoreData.rank;
                
                let assessColor = "rose";
                let assessLabel = "Poor";
                if (rank <= 5) { assessColor = "emerald"; assessLabel = "Excellent"; }
                else if (rank <= 10) { assessColor = "teal"; assessLabel = "Good"; }
                else if (rank <= 20) { assessColor = "amber"; assessLabel = "Acceptable"; }
                
                return (
                  <div className="bg-rose-500/5 border-t border-rose-500/20 mt-2 p-4">
                     <div className="flex items-center gap-2 mb-3">
                        <AlertCircle className="w-4 h-4 text-rose-400" />
                        <span className="text-xs font-bold text-rose-400 uppercase tracking-wider">Actual Result ⭐</span>
                        <span className={`ml-auto text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-${assessColor}-500/20 text-${assessColor}-400 border border-${assessColor}-500/30`}>
                          Assessment: {assessLabel}
                        </span>
                     </div>
                     <div className="flex items-center justify-between">
                       <div className="flex items-center gap-3">
                         <span className="text-xl font-mono font-bold text-white">{actual.home_goals} - {actual.away_goals}</span>
                       </div>
                       <div className="text-right">
                         <span className="block text-xs font-mono font-bold text-slate-300">Rank #{rank}</span>
                         <span className="block text-xs font-mono text-slate-500">{actualScoreData.probability}% Prob</span>
                       </div>
                     </div>
                     <p className="text-[10px] text-slate-500 mt-2 text-center">This scoreline was not inside the Top {scores.length} predictions.</p>
                  </div>
                );
              })()}
            </div>
          </div>
        ))}
      </div>

      {/* Dominance Insights */}
      {dominance && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[{ data: { home_pct: poisson?.result?.home_win || 0, away_pct: poisson?.result?.away_win || 0, expected_home: "Win", expected_total: `Draw: ${poisson?.result?.draw || 0}%`, expected_away: "Win" }, label: "Win Probability", icon: <Trophy className="w-4 h-4 text-emerald-400" />, colorA: "bg-emerald-400", colorB: "bg-purple-400", accent: "text-emerald-400" },
            { data: corners, label: "Corner Dominance", icon: <CornerUpRight className="w-4 h-4 text-blue-400" />, colorA: "bg-blue-400", colorB: "bg-orange-400", accent: "text-blue-400" },
            { data: cards, label: "Card Dominance", icon: <CreditCard className="w-4 h-4 text-amber-400" />, colorA: "bg-amber-400", colorB: "bg-rose-400", accent: "text-amber-400" }
          ].map(({ data, label, icon, colorA, colorB, accent }) => (
            <div key={label} className="bg-[#111318] border border-white/5 rounded-xl p-4 relative overflow-hidden">
              <div className="flex items-center gap-2 mb-3">
                {icon}
                <span className={`text-[10px] font-bold ${accent} uppercase tracking-wider`}>{label}</span>
              </div>
              <div className="flex items-center gap-3 mb-3">
                <div className="flex-1 text-center">
                  <span className="text-[9px] text-slate-500 block mb-0.5">{homeName}</span>
                  <span className={`text-lg font-mono font-bold ${data.home_pct > data.away_pct ? accent : 'text-slate-500'}`}>{data.home_pct}%</span>
                </div>
                <div className="text-[9px] text-slate-600">vs</div>
                <div className="flex-1 text-center">
                  <span className="text-[9px] text-slate-500 block mb-0.5">{awayName}</span>
                  <span className={`text-lg font-mono font-bold ${data.away_pct > data.home_pct ? accent : 'text-slate-500'}`}>{data.away_pct}%</span>
                </div>
              </div>
              <div className="w-full h-2 bg-black/30 rounded-full overflow-hidden flex">
                <div className={`h-full ${colorA} rounded-l-full`} style={{ width: `${data.home_pct}%` }} />
                <div className={`h-full ${colorB} rounded-r-full`} style={{ width: `${data.away_pct}%` }} />
              </div>
              <div className="flex justify-between mt-2">
                <span className="text-[9px] text-slate-600 font-mono">{typeof data.expected_home === 'string' ? data.expected_home : `Exp: ${data.expected_home}`}</span>
                <span className="text-[9px] text-slate-600 font-mono">{typeof data.expected_total === 'string' ? data.expected_total : `Total: ${data.expected_total}`}</span>
                <span className="text-[9px] text-slate-600 font-mono">{typeof data.expected_away === 'string' ? data.expected_away : `Exp: ${data.expected_away}`}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/* ── Calibrated Confidence Chart Component ───────── */
const ConfidenceChartCard = ({ picks, tiers = [] }) => {
  const getIcon = (cat) => {
    switch (cat) {
      case "Result": return <Trophy className="w-3.5 h-3.5 text-emerald-400" />;
      case "Goals": return <Target className="w-3.5 h-3.5 text-cyan-400" />;
      case "Team Goals": return <TrendingUp className="w-3.5 h-3.5 text-teal-400" />;
      case "Handicaps": return <Zap className="w-3.5 h-3.5 text-fuchsia-400" />;
      default: return <Shield className="w-3.5 h-3.5 text-slate-400" />;
    }
  };

  const getBadgeStyle = (cat) => {
    switch (cat) {
      case "Result": return "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20";
      case "Goals": return "bg-cyan-500/15 text-cyan-400 border border-cyan-500/20";
      case "Team Goals": return "bg-teal-500/15 text-teal-400 border border-teal-500/20";
      case "Handicaps": return "bg-fuchsia-500/15 text-fuchsia-400 border border-fuchsia-500/20";
      default: return "bg-slate-500/15 text-slate-400 border border-white/5";
    }
  };

  const fallbackTiers = [
    { id: "tier1", name: "Tier 1", label: "Elite Confidence", range: "80-100%", picks: (picks || []).filter(p => p.probability >= 80) },
    { id: "tier2", name: "Tier 2", label: "Strong Confidence", range: "70-79.9%", picks: (picks || []).filter(p => p.probability >= 70 && p.probability < 80) },
    { id: "tier3", name: "Tier 3", label: "Moderate Confidence", range: "60-69.9%", picks: (picks || []).filter(p => p.probability >= 60 && p.probability < 70) },
  ];
  const displayTiers = tiers.length ? tiers : fallbackTiers;

  const getTierStyle = (id) => {
    switch (id) {
      case "tier1":
        return { border: "border-emerald-500/30", bg: "bg-emerald-500/10", text: "text-emerald-400", icon: <Crown className="w-4 h-4 text-emerald-400" /> };
      case "tier2":
        return { border: "border-blue-500/30", bg: "bg-blue-500/10", text: "text-blue-400", icon: <Shield className="w-4 h-4 text-blue-400" /> };
      default:
        return { border: "border-amber-500/25", bg: "bg-amber-500/10", text: "text-amber-400", icon: <Activity className="w-4 h-4 text-amber-400" /> };
    }
  };

  return (
    <div className="space-y-4">
      {displayTiers.map((tier) => {
        const style = getTierStyle(tier.id);
        const tierPicks = tier.picks || [];

        return (
          <div key={tier.id} className={`bg-[#111318] border ${style.border} rounded-xl overflow-hidden shadow-lg`}>
            {/* Tier Header */}
            <div className={`px-4 py-3 ${style.bg} border-b border-white/5 flex items-center justify-between gap-3`}>
              <div className="flex items-center gap-2">
                {style.icon}
                <div>
                  <span className={`text-xs font-bold uppercase tracking-wider block ${style.text}`}>{tier.name} · {tier.range}</span>
                  <span className={`text-[9px] ${style.text} opacity-70 uppercase tracking-widest`}>{tier.label}</span>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="hidden md:inline-flex bg-black/25 text-slate-300 border border-white/5 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider font-mono">
                  avg {(tier.avg_probability || 0).toFixed(1)}%
                </span>
                <span className={`bg-white/10 ${style.text} text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider font-mono`}>
                  {tierPicks.length} picks
                </span>
                <span className="hidden sm:inline-flex bg-emerald-500/10 text-emerald-400 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider font-mono">
                  {tier.bankroll_qualified_count || 0} passed gate
                </span>
              </div>
            </div>

            {/* Tier List */}
            <div className="divide-y divide-white/[0.04] bg-[#0D0F13]">
              {tierPicks.length === 0 && (
                <div className="p-4 text-[11px] text-slate-600 uppercase tracking-widest">
                  No markets in this tier
                </div>
              )}
              {tierPicks.map((pick, idx) => (
                <div key={idx} className="flex flex-col sm:flex-row sm:items-center justify-between p-4 hover:bg-white/[0.02] transition-colors gap-3">
                  {/* Left: Category Icon & Badge + Market name */}
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <div className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold tracking-wider shrink-0 uppercase ${getBadgeStyle(pick.section)}`}>
                      {getIcon(pick.section)}
                      <span>{pick.section}</span>
                    </div>
                    <span className="text-[13px] font-medium text-slate-200 truncate">{pick.market}</span>
                    {pick.bankroll_qualified && (
                      <span className="hidden md:inline-flex items-center gap-1 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider">
                        <Shield className="w-3 h-3" />
                        Gate
                      </span>
                    )}
                    {pick.fair_odds && (
                      <span className="hidden lg:inline-flex bg-black/25 text-slate-400 border border-white/5 rounded px-1.5 py-0.5 text-[9px] font-mono uppercase">
                        fair {pick.fair_odds}
                      </span>
                    )}
                  </div>

                  {/* Right: Progress bar & probability */}
                  <div className="flex items-center gap-3 shrink-0 sm:w-64">
                    <div className="flex-1 h-2 bg-black/40 rounded-full overflow-hidden hidden sm:block border border-white/5">
                      <div
                        className={`h-full ${getBarColor(pick.probability)} rounded-full animate-grow`}
                        style={{ width: `${Math.min(pick.probability, 100)}%` }}
                      />
                    </div>
                    <div className="flex items-baseline justify-between sm:justify-end sm:gap-2 w-full sm:w-auto font-mono">
                      <span className="text-[9px] text-slate-600 uppercase font-mono sm:hidden">Probability</span>
                      <span className={`text-sm font-bold ${getTextColor(pick.probability)}`}>
                        {pick.probability.toFixed(1)}%
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
};


const MatchDetail = ({ fixture, analysis, loading, error }) => {
  const [activeLayer, setActiveLayer] = useState('layer2');

  const [expandedSections, setExpandedSections] = useState({
    "Goals": true, "First Half": true, "Second Half": true, "Team Goals": true,
    "Result": true, "Handicaps": true, "Corners": true, "Cards": true
  });

  const sectionOrder = useMemo(() => {
    return ["Goals", "First Half", "Second Half", "Team Goals", "Result", "Handicaps", "Corners", "Cards"];
  }, []);

  const toggleSection = (sec) => {
    setExpandedSections(prev => ({ ...prev, [sec]: !prev[sec] }));
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <Loader2 className="w-10 h-10 animate-spin text-emerald-500 mb-4" />
        <p className="text-emerald-400/60 text-xs tracking-[0.2em] uppercase animate-pulse">Computing Modular Analysis...</p>
        <p className="text-slate-600 text-[10px] tracking-widest mt-2 uppercase">7 modules + score estimation + 10 tiers</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8">
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-8 text-center max-w-md">
          <AlertCircle className="w-10 h-10 mx-auto mb-3 text-red-500/60" />
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (!analysis) return null;

  const categories = analysis.categories || [];
  const fullAnalysis = analysis.full_analysis || {};
  const poisson = analysis.poisson;
  const scorePrediction = analysis.score_prediction;
  const dominance = analysis.dominance;
  const homeName = fixture.home_team.name;
  const awayName = fixture.away_team.name;

  const totalFullMarkets = Object.values(fullAnalysis).reduce((sum, items) => sum + items.length, 0);

  return (
    <div className="max-w-4xl mx-auto p-4 sm:p-6 animate-fade-in text-slate-200">

      {/* ── Match Header ───────────────────────── */}
      <div className="bg-[#111318] border border-white/5 rounded-2xl p-6 mb-6 shadow-2xl relative overflow-hidden">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-full bg-emerald-500/5 blur-[80px] pointer-events-none" />
        <div className="flex flex-col sm:flex-row items-center justify-between gap-6 relative z-10">
          <div className="flex-1 text-center sm:text-right">
            <img src={fixture.home_team.logo} alt="" className="w-16 h-16 sm:ml-auto mx-auto mb-3 drop-shadow-2xl" />
            <h2 className="text-lg font-bold text-white tracking-wide">{homeName}</h2>
            {poisson && <span className="text-[10px] text-emerald-400/80 font-mono">xG: {poisson.lambda_home.toFixed(2)}</span>}
          </div>
          <div className="flex flex-col items-center justify-center px-4">
            <span className="text-[10px] text-emerald-400 uppercase tracking-[0.2em] font-bold mb-2">{fixture.status}</span>
            <div className="bg-black/40 border border-white/10 px-4 py-1.5 rounded-full text-xs text-slate-400 font-mono tracking-wider">{fixture.time}</div>
            <span className="text-[9px] text-slate-500 mt-2 uppercase tracking-widest text-center max-w-[120px]">{fixture.league.name}</span>
          </div>
          <div className="flex-1 text-center sm:text-left">
            <img src={fixture.away_team.logo} alt="" className="w-16 h-16 sm:mr-auto mx-auto mb-3 drop-shadow-2xl" />
            <h2 className="text-lg font-bold text-white tracking-wide">{awayName}</h2>
            {poisson && <span className="text-[10px] text-emerald-400/80 font-mono">xG: {poisson.lambda_away.toFixed(2)}</span>}
          </div>
        </div>
      </div>

      {/* ── Engine Stats Banner ──────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 sm:gap-4 mb-6 px-4 py-3 bg-[#111318] border border-white/5 rounded-xl shadow-lg">
        <div className="flex items-center gap-1.5">
          <Cpu className="w-4 h-4 text-slate-400" />
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Engine v6</span>
        </div>
        <div className="h-4 w-px bg-white/10" />
        <span className="text-[10px] text-slate-500 uppercase tracking-widest">
          <span className="text-white font-mono font-bold mr-1">{analysis.total_markets_scanned}</span> MARKETS
        </span>
        <div className="h-4 w-px bg-white/10" />
        <span className="text-[10px] text-emerald-400 uppercase tracking-widest font-bold flex items-center gap-1">
          <Shield className="w-3 h-3" />
          <span className="text-white font-mono font-bold mr-0.5">{analysis.total_confident_picks || analysis.top_picks?.length || 0}</span> CONFIDENT PICKS (≥60%)
        </span>
        <div className="h-4 w-px bg-white/10" />
        <span className="text-[10px] text-emerald-400 uppercase tracking-widest font-bold flex items-center gap-1">
          <Activity className="w-3 h-3" />
          {analysis.total_qualified} QUALIFIED (≥80%)
        </span>
      </div>

      {/* ── Score Prediction + Dominance ──────────────── */}
      <ScorePrediction 
        scorePrediction={scorePrediction} 
        dominance={dominance} 
        homeName={homeName} 
        awayName={awayName} 
        poisson={poisson}
        actual={fixture?.actual || (fixture?.status === 'FT' && fixture?.home_goals != null ? {
          home_goals: fixture.home_goals,
          away_goals: fixture.away_goals,
          total_goals: fixture.home_goals + fixture.away_goals
        } : null)}
        status={fixture?.status} 
      />

      {/* ── Layer Toggle ──────────────────────── */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setActiveLayer('layer2')}
          className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl border transition-all duration-200 ${activeLayer === 'layer2'
            ? 'bg-gradient-to-r from-emerald-500/10 to-teal-500/10 border-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.08)]'
            : 'bg-[#111318] border-white/5 hover:border-white/10'
            }`}
        >
          <Shield className={`w-4 h-4 ${activeLayer === 'layer2' ? 'text-emerald-400' : 'text-slate-500'}`} />
          <div className="text-left">
            <span className={`text-xs font-bold uppercase tracking-wider block ${activeLayer === 'layer2' ? 'text-white' : 'text-slate-400'}`}>
              Layer 2 — Confident Picks
            </span>
            <span className={`text-[9px] ${activeLayer === 'layer2' ? 'text-emerald-400/70' : 'text-slate-600'}`}>
              3 rank tiers (≥60%)
            </span>
          </div>
          {activeLayer === 'layer2' && (
            <span className="bg-emerald-500/20 text-emerald-400 text-[10px] font-bold px-2 py-0.5 rounded-md ml-auto">
              {analysis.total_confident_picks || analysis.top_picks?.length || 0}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveLayer('layer1')}
          className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl border transition-all duration-200 ${activeLayer === 'layer1'
            ? 'bg-gradient-to-r from-blue-500/15 to-violet-500/10 border-blue-500/40 shadow-[0_0_20px_rgba(59,130,246,0.1)]'
            : 'bg-[#111318] border-white/5 hover:border-white/10'
            }`}
        >
          <Layers className={`w-4 h-4 ${activeLayer === 'layer1' ? 'text-blue-400' : 'text-slate-500'}`} />
          <div className="text-left">
            <span className={`text-xs font-bold uppercase tracking-wider block ${activeLayer === 'layer1' ? 'text-white' : 'text-slate-400'}`}>
              Layer 1 — Full Analysis
            </span>
            <span className={`text-[9px] ${activeLayer === 'layer1' ? 'text-blue-400/70' : 'text-slate-600'}`}>
              all modules • all probabilities
            </span>
          </div>
          {activeLayer === 'layer1' && (
            <span className="bg-blue-500/20 text-blue-400 text-[10px] font-bold px-2 py-0.5 rounded-md ml-auto">{totalFullMarkets}</span>
          )}
        </button>
      </div>

      {/* ══════════════════════════════════════════ */}
      {/* LAYER 2 — CONFIDENT PICKS                 */}
      {/* ══════════════════════════════════════════ */}
      {activeLayer === 'layer2' && (
        <div className="animate-fade-in space-y-4">
          <ConfidenceChartCard picks={analysis.top_picks} tiers={analysis.tiers || []} />
        </div>
      )}

      {/* ══════════════════════════════════════════ */}
      {/* LAYER 1 — FULL STRUCTURED ANALYSIS        */}
      {/* ══════════════════════════════════════════ */}
      {activeLayer === 'layer1' && (
        <div className="animate-fade-in">
          <div className="flex items-center gap-2 mb-2">
            <Layers className="w-5 h-5 text-blue-400" />
            <h3 className="text-sm font-bold tracking-[0.15em] text-white uppercase mt-0.5">Full Structured Analysis</h3>
          </div>
          <p className="text-[10px] text-slate-500 uppercase tracking-widest mb-6">
            Each module analyzed independently — no mixing between categories
          </p>

          <div className="space-y-4">
            {sectionOrder.map((secName) => {
              const items = fullAnalysis[secName] || [];
              if (items.length === 0) return null;
              const isExpanded = expandedSections[secName];
              const qualifiedCount = items.filter(i => i.probability >= 80).length;

              return (
                <div key={secName} className="bg-[#111318] border border-white/5 rounded-xl overflow-hidden shadow-md relative">
                  <div className={`absolute inset-0 bg-gradient-to-r ${getSectionGradient(secName)} pointer-events-none`} />
                  <button onClick={() => toggleSection(secName)} className="w-full flex items-center justify-between p-4 hover:bg-white/[0.02] transition-colors relative z-10">
                    <div className="flex items-center gap-3">
                      <div className="bg-white/5 p-1.5 rounded-md">{getSectionIcon(secName)}</div>
                      <span className="text-sm font-bold text-white uppercase tracking-wider">{secName}</span>
                      <span className="bg-white/5 text-slate-400 text-[10px] font-mono px-2 py-0.5 rounded-lg">{items.length} markets</span>
                      {qualifiedCount > 0 && (
                        <span className="bg-emerald-500/10 text-emerald-400 text-[10px] font-mono px-2 py-0.5 rounded-lg">{qualifiedCount} ≥80%</span>
                      )}
                    </div>
                    {isExpanded ? <ChevronDown className="w-5 h-5 text-slate-500" /> : <ChevronRight className="w-5 h-5 text-slate-500" />}
                  </button>

                  {isExpanded && (
                    <div className="border-t border-white/5 bg-[#0D0F13] relative z-10">
                      {items.map((item, idx) => {
                        const isQualified = item.probability >= 80;
                        return (
                          <div key={idx} className={`flex items-center justify-between p-3.5 px-5 border-b border-white/[0.02] last:border-0 transition-colors ${isQualified ? 'bg-emerald-500/[0.03] hover:bg-emerald-500/[0.06]' : 'hover:bg-white/[0.01]'}`}>
                            <div className="flex items-center gap-2">
                              {isQualified && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0 animate-pulse" />}
                              <span className={`text-[13px] font-medium ${isQualified ? 'text-white' : 'text-slate-400'}`}>{item.market}</span>
                            </div>
                            <div className="flex items-center gap-3">
                              <div className="w-20 sm:w-28 h-1.5 bg-black/40 rounded-full overflow-hidden hidden sm:block">
                                <div className={`h-full ${getBarColor(item.probability)} rounded-full animate-grow`} style={{ width: `${Math.min(item.probability, 100)}%` }} />
                              </div>
                              <span className={`text-[13px] font-mono font-bold min-w-[50px] text-right ${getTextColor(item.probability)}`}>{item.probability.toFixed(1)}%</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

export default MatchDetail;
