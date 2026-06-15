import React, { useState } from 'react';

/** Team logo with lazy loading, correct sizing, and a text fallback. */
const TeamLogo = ({ src, name, size = 28 }) => {
  const [failed, setFailed] = useState(false);
  const initials = name
    ? name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : '?';

  if (failed || !src) {
    return (
      <div
        style={{ width: size, height: size, minWidth: size }}
        className="rounded-full bg-white/8 flex items-center justify-center text-[9px] font-bold text-slate-400 shrink-0 select-none"
      >
        {initials}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={name}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{ width: size, height: size, minWidth: size }}
      className="object-contain shrink-0"
    />
  );
};

/** Status badge — NS / LIVE mm' / FT / STALE */
const StatusBadge = ({ status, time, is_stale }) => {
  if (is_stale || status === 'STALE') {
    return (
      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-red-900/40 text-red-400 border border-red-500/30 shrink-0">
        STALE
      </span>
    );
  }

  const isLive = status && (status.startsWith('LIVE') || status === '1H' || status === '2H' || status === 'HT');
  const isFt   = status === 'FT';

  if (isLive) return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 shrink-0">
      <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />
      {status}
    </span>
  );

  if (isFt) return (
    <span className="text-[10px] font-semibold text-slate-500 tracking-wide shrink-0">FT</span>
  );

  return (
    <span className="text-[10px] text-slate-500 tabular-nums shrink-0">{time || 'TBD'}</span>
  );
};

const MatchRow = ({ match, isSelected, onClick }) => {
  const hasScore = match.home_goals !== null && match.home_goals !== undefined;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-white/[0.04] transition-all duration-150 group relative ${
        isSelected
          ? 'bg-gold-500/10 border-l-2 border-l-gold-500'
          : 'hover:bg-white/[0.03] border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center gap-2.5">
        {/* Kickoff / Status */}
        <div className="w-[44px] shrink-0 flex flex-col items-center justify-center">
          <StatusBadge status={match.status} time={match.time} is_stale={match.is_stale || match._stale} />
        </div>

        {/* Teams */}
        <div className="flex-1 min-w-0 space-y-1">
          {/* Home */}
          <div className="flex items-center gap-2">
            <TeamLogo src={match.home_team.logo} name={match.home_team.name} size={22} />
            <span className={`text-[13px] truncate leading-tight flex-1 ${isSelected ? 'text-white font-medium' : 'text-slate-200'}`}>
              {match.home_team.name}
            </span>
            {hasScore && (
              <span className={`text-[13px] font-bold tabular-nums ml-auto pl-1 ${
                isSelected ? 'text-gold-400' :
                match.home_goals > match.away_goals ? 'text-white' : 'text-slate-400'
              }`}>
                {match.home_goals}
              </span>
            )}
          </div>

          {/* Away */}
          <div className="flex items-center gap-2">
            <TeamLogo src={match.away_team.logo} name={match.away_team.name} size={22} />
            <span className={`text-[13px] truncate leading-tight flex-1 ${isSelected ? 'text-white font-medium' : 'text-slate-300'}`}>
              {match.away_team.name}
            </span>
            {hasScore && (
              <span className={`text-[13px] font-bold tabular-nums ml-auto pl-1 ${
                isSelected ? 'text-gold-400' :
                match.away_goals > match.home_goals ? 'text-white' : 'text-slate-400'
              }`}>
                {match.away_goals}
              </span>
            )}
          </div>

          {/* League Info */}
          <div className="flex items-center gap-1.5 mt-1.5 opacity-60">
            <img 
              src={match.league.logo} 
              alt="" 
              className="w-3 h-3 object-contain"
              onError={e => { e.target.style.display = 'none'; }}
            />
            <span className="text-[9px] uppercase tracking-wider truncate">
              {match.league.name} {match.league.country && `• ${match.league.country}`}
            </span>
          </div>
        </div>

        {/* Selected indicator */}
        <div className={`w-0.5 h-9 rounded-full shrink-0 transition-colors ${isSelected ? 'bg-gold-500' : 'bg-transparent group-hover:bg-white/10'}`} />
      </div>
    </button>
  );
};

export default MatchRow;
