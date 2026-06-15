import React from 'react';

/** Surface badge with color coding */
const SurfaceBadge = ({ surface }) => {
  const colors = {
    hard:    'bg-blue-500/20 text-blue-300 border-blue-500/30',
    clay:    'bg-orange-500/20 text-orange-300 border-orange-500/30',
    grass:   'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    unknown: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
  };
  const cls = colors[surface?.toLowerCase()] || colors.unknown;
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wider border ${cls} shrink-0`}>
      {surface || '?'}
    </span>
  );
};

/** Status badge for NS / LIVE / FT / STALE */
const TennisStatusBadge = ({ status, is_stale }) => {
  if (is_stale) {
    return (
      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-red-900/40 text-red-400 border border-red-500/30 shrink-0">
        STALE
      </span>
    );
  }
  if (status === 'LIVE') {
    return (
      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 shrink-0">
        <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />
        LIVE
      </span>
    );
  }
  if (status === 'FT') {
    return <span className="text-[10px] font-semibold text-slate-500 shrink-0">FT</span>;
  }
  return <span className="text-[10px] text-slate-500 shrink-0">{status || 'NS'}</span>;
};

const TennisMatchRow = ({ match, isSelected, onClick }) => {
  const hasScore = match.sets_1 > 0 || match.sets_2 > 0;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-white/[0.04] transition-all duration-150 group relative ${
        isSelected
          ? 'bg-emerald-500/10 border-l-2 border-l-emerald-500'
          : 'hover:bg-white/[0.03] border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center gap-2.5">
        {/* Status */}
        <div className="w-[44px] shrink-0 flex flex-col items-center justify-center gap-0.5">
          <TennisStatusBadge status={match.status} is_stale={match.is_stale} />
        </div>

        {/* Players + Score */}
        <div className="flex-1 min-w-0 space-y-1">
          {/* Player 1 */}
          <div className="flex items-center gap-2">
            <span className={`text-[13px] truncate leading-tight flex-1 ${
              isSelected ? 'text-white font-medium' : 'text-slate-200'
            }`}>
              {match.player_1 || '—'}
            </span>
            {hasScore && (
              <span className={`text-[13px] font-bold tabular-nums ml-auto pl-1 ${
                match.sets_1 > match.sets_2 ? 'text-white' : 'text-slate-400'
              }`}>
                {match.sets_1}
              </span>
            )}
          </div>

          {/* Player 2 */}
          <div className="flex items-center gap-2">
            <span className={`text-[13px] truncate leading-tight flex-1 ${
              isSelected ? 'text-white font-medium' : 'text-slate-300'
            }`}>
              {match.player_2 || '—'}
            </span>
            {hasScore && (
              <span className={`text-[13px] font-bold tabular-nums ml-auto pl-1 ${
                match.sets_2 > match.sets_1 ? 'text-white' : 'text-slate-400'
              }`}>
                {match.sets_2}
              </span>
            )}
          </div>

          {/* Tournament + Surface */}
          <div className="flex items-center gap-1.5 mt-1 opacity-70">
            <SurfaceBadge surface={match.surface} />
            <span className="text-[9px] uppercase tracking-wider truncate text-slate-400">
              {match.tournament || 'Unknown Tournament'}
            </span>
          </div>
        </div>

        {/* Selection indicator */}
        <div className={`w-0.5 h-9 rounded-full shrink-0 transition-colors ${
          isSelected ? 'bg-emerald-500' : 'bg-transparent group-hover:bg-white/10'
        }`} />
      </div>
    </button>
  );
};

export default TennisMatchRow;
