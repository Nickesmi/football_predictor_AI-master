import React from 'react';

const MatchRow = ({ match, isSelected, onClick }) => {
  const hasScore = match.home_goals !== null && match.home_goals !== undefined;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-border transition-all duration-200 group relative ${isSelected
          ? 'bg-gold-500/8 border-l-2 border-l-gold-500'
          : 'hover:bg-white/[0.03] border-l-2 border-l-transparent'
        }`}
    >
      {/* Time column */}
      <div className="flex items-center gap-3">
        <div className={`w-12 text-center shrink-0 ${match.status === 'LIVE' || match.status === '1H' || match.status === '2H'
            ? 'text-red-500 font-bold text-xs'
            : isSelected ? 'text-gold-500 font-semibold text-xs' : 'text-slate-500 text-xs'
          }`}>
          {match.status === 'FT' ? 'FT' : match.time}
        </div>

        {/* Teams & League */}
        <div className="flex-1 min-w-0 space-y-1.5 pt-1">
          {/* League Info */}
          <div className="flex items-center gap-1.5 mb-2 opacity-50">
            <img src={match.league.logo} alt="" className="w-3 h-3 object-contain grayscale" />
            <span className="text-[9px] font-semibold text-slate-300 uppercase tracking-widest truncate">
              {match.league.name}
            </span>
          </div>
          {/* Home */}
          <div className="flex items-center gap-2.5">
            <img src={match.home_team.logo} alt="" className="w-4 h-4 object-contain shrink-0" />
            <span className={`text-sm truncate ${isSelected ? 'text-white font-medium' : 'text-slate-300'}`}>
              {match.home_team.name}
            </span>
            {hasScore && (
              <span className={`ml-auto text-sm font-bold tabular-nums ${isSelected ? 'text-white' : 'text-slate-300'}`}>
                {match.home_goals}
              </span>
            )}
          </div>
          {/* Away */}
          <div className="flex items-center gap-2.5">
            <img src={match.away_team.logo} alt="" className="w-4 h-4 object-contain shrink-0" />
            <span className={`text-sm truncate ${isSelected ? 'text-white font-medium' : 'text-slate-300'}`}>
              {match.away_team.name}
            </span>
            {hasScore && (
              <span className={`ml-auto text-sm font-bold tabular-nums ${isSelected ? 'text-white' : 'text-slate-300'}`}>
                {match.away_goals}
              </span>
            )}
          </div>
        </div>

        {/* Arrow indicator */}
        <div className={`w-1 h-8 rounded-full shrink-0 transition-colors ${isSelected ? 'bg-gold-500' : 'bg-transparent group-hover:bg-white/10'}`} />
      </div>
    </button>
  );
};

export default MatchRow;
