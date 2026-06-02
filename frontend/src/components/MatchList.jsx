import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Search } from 'lucide-react';
import MatchRow from './MatchRow';
import ProfitModeCard from './ProfitModeCard';
import { isMatchLive } from '../utils/matchTime';
import { getMatchSection, SECTION_ORDER } from '../utils/matchStatus';

function sortWithinSection(matches) {
  return [...matches].sort((a, b) => {
    if (isMatchLive(a) && !isMatchLive(b)) return -1;
    if (!isMatchLive(a) && isMatchLive(b)) return 1;
    return (a.time || '').localeCompare(b.time || '');
  });
}

export default function MatchList({
  fixtures,
  selectedFixtureId,
  selectedFixture,
  onSelectMatch,
  liveNow,
  meta,
}) {
  const [query, setQuery] = useState('');
  const [openSections, setOpenSections] = useState(() =>
    Object.fromEntries(SECTION_ORDER.map((s) => [s.id, s.defaultOpen])),
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return fixtures;
    return fixtures.filter((m) =>
      [m.league?.name, m.home_team?.name, m.away_team?.name]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(q),
    );
  }, [fixtures, query]);

  const bySection = useMemo(() => {
    const buckets = { live: [], upcoming: [], pending: [], finished: [] };
    for (const m of filtered) {
      buckets[getMatchSection(m)].push(m);
    }
    for (const key of Object.keys(buckets)) {
      buckets[key] = sortWithinSection(buckets[key]);
    }
    return buckets;
  }, [filtered]);

  const toggleSection = (id) => {
    setOpenSections((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="flex flex-col min-h-0">
      <div className="px-4 py-4 border-b border-white/5 space-y-3">
        <p className="text-xs text-slate-500">
          {filtered.length} matches
          {meta?.primary_source ? ` · ${meta.primary_source}` : ''}
        </p>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search…"
            className="w-full rounded-lg border border-white/10 bg-transparent py-2 pl-9 pr-3 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-white/20"
          />
        </div>
      </div>

      <ProfitModeCard fixture={selectedFixture} defaultOpen />

      <div className="flex-1 space-y-4">
        {filtered.length === 0 ? (
          <p className="px-5 py-12 text-center text-sm text-slate-500">No matches found.</p>
        ) : (
          SECTION_ORDER.map(({ id, title }) => {
            const matches = bySection[id];
            if (!matches.length) return null;
            const isOpen = openSections[id];
            return (
              <section key={id} className="border-b border-white/5 bg-surface-0">
                <button
                  type="button"
                  onClick={() => toggleSection(id)}
                  className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-white/[0.02]"
                >
                  {isOpen ? (
                    <ChevronDown className="w-4 h-4 text-slate-500" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-slate-500" />
                  )}
                  <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex-1">
                    {title}
                  </span>
                  <span className="text-xs text-slate-500 tabular-nums">{matches.length}</span>
                </button>
                {isOpen && (
                  <div className="space-y-1">
                    {matches.map((match) => (
                      <MatchRow
                        key={match.id}
                        match={match}
                        isSelected={match.id === selectedFixtureId}
                        onClick={() => onSelectMatch(match)}
                        liveNow={liveNow}
                      />
                    ))}
                  </div>
                )}
              </section>
            );
          })
        )}
      </div>
    </div>
  );
}
