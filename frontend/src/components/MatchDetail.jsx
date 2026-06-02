import React, { useState } from 'react';
import { Loader2, AlertCircle, ArrowRight, ShieldAlert, Cpu, ChevronDown, ChevronRight, Activity, Zap, Target, CornerUpRight, CreditCard, Trophy } from 'lucide-react';

const getBarColor = (pct) => {
  if (pct >= 95) return "bg-emerald-500";
  if (pct >= 90) return "bg-green-500";
  if (pct >= 85) return "bg-lime-500";
  if (pct >= 80) return "bg-yellow-500";
  return "bg-slate-500";
};

const MatchDetail = ({ fixture, analysis, loading, error }) => {
  const [expandedSections, setExpandedSections] = useState({
    "Goals": true,
    "Team Goals": false,
    "First Half": false,
    "Corners": false,
    "Cards": false,
    "Handicaps": false,
    "Result": false
  });

  const toggleSection = (sec) => {
    setExpandedSections(prev => ({ ...prev, [sec]: !prev[sec] }));
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <Loader2 className="w-10 h-10 animate-spin text-emerald-500 mb-4" />
        <p className="text-emerald-400/60 text-xs tracking-[0.2em] uppercase animate-pulse">Computing Match Probabilities...</p>
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

  const topPicks = analysis.top_picks || [];
  const sections = analysis.sections || {};
  const poisson = analysis.poisson;

  // Reorder sections based on user prompt
  const sectionOrder = ["Goals", "Team Goals", "First Half", "Corners", "Cards", "Handicaps", "Result"];

  return (
    <div className="max-w-3xl mx-auto p-4 sm:p-6 animate-fade-in text-slate-200">
      
      {/* ── Match Header (Dashboard Style) ───────────────────────── */}
      <div className="bg-[#111318] border border-white/5 rounded-2xl p-6 mb-6 shadow-2xl relative overflow-hidden">
        {/* Subtle background glow */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-full bg-emerald-500/5 blur-[80px] pointer-events-none" />
        
        <div className="flex flex-col sm:flex-row items-center justify-between gap-6 relative z-10">
          <div className="flex-1 text-center sm:text-right">
            <img src={fixture.home_team.logo} alt="" className="w-16 h-16 sm:ml-auto mx-auto mb-3 drop-shadow-2xl" />
            <h2 className="text-lg font-bold text-white tracking-wide">{fixture.home_team.name}</h2>
            {poisson && <span className="text-[10px] text-emerald-400/80 font-mono">xG: {poisson.lambda_home.toFixed(2)}</span>}
          </div>
          
          <div className="flex flex-col items-center justify-center px-4">
            <span className="text-[10px] text-emerald-400 uppercase tracking-[0.2em] font-bold mb-2">{fixture.status}</span>
            <div className="bg-black/40 border border-white/10 px-4 py-1.5 rounded-full text-xs text-slate-400 font-mono tracking-wider">
              {fixture.time}
            </div>
            <span className="text-[9px] text-slate-500 mt-2 uppercase tracking-widest text-center max-w-[120px]">{fixture.league.name}</span>
          </div>

          <div className="flex-1 text-center sm:text-left">
            <img src={fixture.away_team.logo} alt="" className="w-16 h-16 sm:mr-auto mx-auto mb-3 drop-shadow-2xl" />
            <h2 className="text-lg font-bold text-white tracking-wide">{fixture.away_team.name}</h2>
            {poisson && <span className="text-[10px] text-emerald-400/80 font-mono">xG: {poisson.lambda_away.toFixed(2)}</span>}
          </div>
        </div>
      </div>

      {/* ── Engine Stats Banner ──────────────────────── */}
      <div className="flex items-center gap-4 mb-8 px-4 py-3 bg-[#111318] border border-white/5 rounded-xl shadow-lg">
        <div className="flex items-center gap-1.5">
          <Cpu className="w-4 h-4 text-slate-400" />
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Analysis Engine</span>
        </div>
        <div className="h-4 w-px bg-white/10" />
        <span className="text-[10px] text-slate-500 uppercase tracking-widest">
          <span className="text-white font-mono font-bold mr-1">{analysis.total_markets_scanned}</span> MARKETS
        </span>
        <div className="h-4 w-px bg-white/10" />
        <span className="text-[10px] text-emerald-400 uppercase tracking-widest font-bold flex items-center gap-1">
          <Activity className="w-3 h-3" />
          {analysis.total_qualified} QUALIFIED (≥80%)
        </span>
      </div>

      {/* ── Top Picks Section (Highlighted Boxes) ─────── */}
      <div className="mb-10">
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-5 h-5 text-yellow-400 fill-yellow-400/20" />
          <h3 className="text-sm font-bold tracking-[0.15em] text-white uppercase mt-0.5">Top Picks</h3>
          <span className="bg-yellow-400/10 text-yellow-400 text-[9px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider ml-auto">
            ≥ 80% Prob Only
          </span>
        </div>

        {topPicks.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {topPicks.map((pick, idx) => (
              <div key={idx} className="bg-gradient-to-br from-[#1A1D24] to-[#14161C] border border-emerald-500/30 rounded-xl p-4 shadow-[0_0_15px_rgba(16,185,129,0.05)] hover:border-emerald-400/60 transition-colors group relative overflow-hidden">
                <div className="absolute top-0 right-0 w-16 h-16 bg-emerald-500/10 rounded-bl-full blur-[20px] pointer-events-none group-hover:bg-emerald-500/20 transition-all" />
                
                <span className="text-[9px] text-emerald-400/70 font-bold uppercase tracking-wider mb-1 block">
                  {pick.section}
                </span>
                <p className="text-sm font-semibold text-white mb-4 line-clamp-2 h-10">{pick.market}</p>
                
                <div className="flex items-end justify-between mt-auto">
                  <div className="w-full">
                    <div className="flex justify-between items-center mb-1.5">
                      <span className="text-[10px] text-slate-500 uppercase font-mono">Win Prob</span>
                      <span className="text-base font-bold font-mono text-emerald-400 leading-none">{pick.probability.toFixed(1)}%</span>
                    </div>
                    <div className="w-full h-1.5 bg-black/40 rounded-full overflow-hidden">
                      <div className={`h-full ${getBarColor(pick.probability)} rounded-full`} style={{ width: `${Math.min(pick.probability, 100)}%` }} />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="bg-[#111318] border border-white/5 rounded-xl p-8 text-center">
            <ShieldAlert className="w-8 h-8 mx-auto mb-3 text-slate-600" />
            <p className="text-sm text-slate-400 font-semibold uppercase tracking-wider">No Valid Top Picks</p>
            <p className="text-xs text-slate-600 mt-2">Zero markets met the strict ≥80% safety criteria for this fixture.</p>
          </div>
        )}
      </div>

      {/* ── Categorized Sections ──────────────────────── */}
      <div className="space-y-4">
        <h3 className="text-xs font-bold tracking-[0.2em] text-slate-500 uppercase mb-4 px-1 pb-2 border-b border-white/5">Detailed Market Breakdown</h3>
        
        {sectionOrder.map((secName) => {
          const items = sections[secName] || [];
          if (items.length === 0) return null;
          
          const isExpanded = expandedSections[secName];

          return (
            <div key={secName} className="bg-[#111318] border border-white/5 rounded-xl overflow-hidden shadow-md">
              <button 
                onClick={() => toggleSection(secName)}
                className="w-full flex items-center justify-between p-4 hover:bg-white/[0.02] transition-colors"
              >
                <div className="flex items-center gap-3">
                  <div className="bg-white/5 p-1.5 rounded-md">
                    {/* Just simple dynamic icons based on name roughly */}
                    {secName === "Goals" || secName === "Team Goals" ? <Target className="w-4 h-4 text-cyan-400" /> :
                     secName === "First Half" ? <Activity className="w-4 h-4 text-violet-400" /> :
                     secName === "Corners" ? <CornerUpRight className="w-4 h-4 text-blue-400" /> :
                     secName === "Cards" ? <CreditCard className="w-4 h-4 text-amber-400" /> :
                     secName === "Handicaps" ? <Activity className="w-4 h-4 text-rose-400" /> :
                     <Trophy className="w-4 h-4 text-emerald-400" />}
                  </div>
                  <span className="text-sm font-bold text-white uppercase tracking-wider">{secName}</span>
                  <span className="bg-white/5 text-slate-400 text-[10px] font-mono px-2 py-0.5 rounded-lg">{items.length}</span>
                </div>
                {isExpanded ? <ChevronDown className="w-5 h-5 text-slate-500" /> : <ChevronRight className="w-5 h-5 text-slate-500" />}
              </button>

              {isExpanded && (
                <div className="border-t border-white/5 bg-[#0D0F13]">
                  {items.map((item, idx) => (
                    <div key={idx} className="flex items-center justify-between p-3.5 px-5 border-b border-white/[0.02] last:border-0 hover:bg-white/[0.01]">
                      <span className="text-[13px] font-medium text-slate-300">{item.market}</span>
                      <div className="flex items-center gap-3">
                        <div className="w-16 sm:w-24 h-1 bg-black/40 rounded-full overflow-hidden hidden sm:block">
                          <div className={`h-full ${getBarColor(item.probability)}`} style={{ width: `${Math.min(item.probability, 100)}%` }} />
                        </div>
                        <span className="text-[13px] font-mono font-bold text-emerald-400 min-w-[50px] text-right">
                          {item.probability.toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

    </div>
  );
};

export default MatchDetail;
