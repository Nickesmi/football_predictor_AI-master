import React from 'react';
import { AlertCircle, TrendingUp, Zap } from 'lucide-react';

const confidenceColor = (c) => {
  if (c === 'HIGH')   return 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30';
  if (c === 'MEDIUM') return 'text-amber-400 bg-amber-500/15 border-amber-500/30';
  return 'text-slate-400 bg-white/5 border-white/10';
};

const DataQualityBar = ({ score }) => {
  const pct = Math.min(100, Math.max(0, score || 0));
  const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[10px] text-slate-400 tabular-nums w-8">{pct.toFixed(0)}/100</span>
    </div>
  );
};

const ProbabilityBar = ({ p1, p2, label1, label2 }) => (
  <div className="space-y-1">
    <div className="flex justify-between text-[10px] text-slate-400 mb-1">
      <span className="truncate max-w-[45%]">{label1}</span>
      <span className="truncate max-w-[45%] text-right">{label2}</span>
    </div>
    <div className="flex h-2 rounded-full overflow-hidden gap-px">
      <div
        className="bg-emerald-500 transition-all"
        style={{ width: `${p1}%` }}
      />
      <div
        className="bg-slate-600 transition-all"
        style={{ width: `${p2}%` }}
      />
    </div>
    <div className="flex justify-between text-[11px] font-bold">
      <span className="text-emerald-400">{p1?.toFixed(1)}%</span>
      <span className="text-slate-400">{p2?.toFixed(1)}%</span>
    </div>
  </div>
);

const TennisPredictionCard = ({ match, prediction, loading, error }) => {
  if (loading) {
    return (
      <div className="flex items-center justify-center p-8 text-slate-500 text-sm gap-2">
        <div className="w-4 h-4 border-2 border-emerald-500/30 border-t-emerald-500 rounded-full animate-spin" />
        Loading prediction...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 p-4 bg-red-900/20 border border-red-500/20 rounded-lg text-red-400 text-sm">
        <AlertCircle className="w-4 h-4 shrink-0" />
        {error}
      </div>
    );
  }

  if (!prediction) {
    return (
      <div className="p-4 text-center text-slate-500 text-sm">
        Select a match to view prediction
      </div>
    );
  }

  const mw = prediction.predictions?.match_winner || prediction.match_winner;
  const topPicks = prediction.top_picks || [];
  const warnings = prediction.warnings || [];
  const dq = prediction.data_quality ?? 0;

  return (
    <div className="space-y-4 animate-fade-in">
      {/* ── STALE warning ─────────────────────────────────── */}
      {match?.is_stale && (
        <div className="flex items-center gap-2 px-3 py-2 bg-red-900/20 border border-red-500/20 rounded-lg text-red-400 text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>Live data unavailable — showing last known state</span>
          {match.provider_error && <span className="text-red-400/60 ml-auto">{match.provider_error}</span>}
        </div>
      )}

      {/* ── Data Quality ──────────────────────────────────── */}
      <div className="bg-[#111318] border border-white/5 rounded-xl p-3 space-y-1">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Data Quality</span>
          <span className="text-[10px] text-slate-400">{prediction.model_version || 'v1.0-elo'}</span>
        </div>
        <DataQualityBar score={dq} />
        {dq < 40 && (
          <p className="text-[9px] text-amber-400/80 mt-1">
            Low quality — predictions strongly shrunk toward 50%
          </p>
        )}
      </div>

      {/* ── Match Winner ──────────────────────────────────── */}
      {mw && (
        <div className="bg-[#111318] border border-white/5 rounded-xl p-4">
          <div className="flex items-center gap-1.5 mb-3">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-[11px] font-bold text-slate-300 uppercase tracking-wider">Match Winner</span>
          </div>
          <ProbabilityBar
            p1={mw.player_1_win}
            p2={mw.player_2_win}
            label1={match?.player_1 || 'Player 1'}
            label2={match?.player_2 || 'Player 2'}
          />
          <div className="flex gap-2 mt-2">
            <div className="flex-1 text-center">
              <span className="text-[9px] text-slate-500">Fair Odds</span>
              <div className="text-[11px] font-bold text-white">{mw.fair_odds_p1?.toFixed(2)}</div>
            </div>
            <div className="flex-1 text-center">
              <span className="text-[9px] text-slate-500">Confidence</span>
              <div className={`text-[11px] font-bold px-2 py-0.5 rounded border text-center ${confidenceColor(mw.confidence)}`}>
                {mw.confidence}
              </div>
            </div>
            <div className="flex-1 text-center">
              <span className="text-[9px] text-slate-500">Fair Odds</span>
              <div className="text-[11px] font-bold text-white">{mw.fair_odds_p2?.toFixed(2)}</div>
            </div>
          </div>
        </div>
      )}

      {/* ── Top Picks ─────────────────────────────────────── */}
      {topPicks.length > 0 && (
        <div className="bg-[#111318] border border-white/5 rounded-xl p-4">
          <div className="flex items-center gap-1.5 mb-3">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-[11px] font-bold text-slate-300 uppercase tracking-wider">Top Picks</span>
          </div>
          <div className="space-y-2">
            {topPicks.map((pick, i) => (
              <div key={i} className="flex items-center gap-2 bg-white/[0.03] rounded-lg px-3 py-2">
                <div className="flex-1 min-w-0">
                  <div className="text-[10px] text-slate-400">{pick.market}</div>
                  <div className="text-[13px] font-bold text-white truncate">{pick.selection}</div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-[11px] font-bold text-emerald-400">{pick.probability?.toFixed(1)}%</div>
                  <div className="text-[9px] text-slate-500">@ {pick.fair_odds?.toFixed(2)}</div>
                </div>
                <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${confidenceColor(pick.confidence)}`}>
                  {pick.confidence}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Warnings ──────────────────────────────────────── */}
      {warnings.length > 0 && (
        <div className="space-y-1">
          {warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[10px] text-amber-400/80 px-1">
              <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
              {w}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default TennisPredictionCard;
