import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Trophy, Loader2, AlertCircle, Calendar, Target } from 'lucide-react';
import MatchRow from './components/MatchRow';
import MatchDetail from './components/MatchDetail';
import DatePicker from './components/DatePicker';
import ResultsTracker from './components/ResultsTracker';
import TennisResultsTracker from './components/TennisResultsTracker';
import TennisView from './views/TennisView';

const API = import.meta.env.VITE_API_URL || "/api";

const fmtDateInTimeZone = (d, timeZone = 'Europe/Istanbul') => {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(d);
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
};

const getRealtimeDate = () => fmtDateInTimeZone(new Date());
const parseMatchDateTime = (fixture) => {
  if (!fixture?.date || !/^\d{2}:\d{2}$/.test(fixture.time || '')) return null;
  const parsed = new Date(`${fixture.date}T${fixture.time}:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const isActiveStatus = (status = '') => (
  status.includes('LIVE') ||
  status.includes('1H') ||
  status.includes('2H') ||
  status.includes('ET') ||
  status.includes('P') ||
  status.includes('HT') ||
  status.includes('BT')
);

const getLiveClockStatus = (fixture, nowMs) => {
  const status = fixture?.status || '';
  if (!isActiveStatus(status) || status === 'HT' || status.includes('BT')) return status;
  return 'LIVE';
};

const withRealtimeStatus = (fixture, nowMs) => ({
  ...fixture,
  status: getLiveClockStatus(fixture, nowMs),
});

const fixtureRealtimeKey = (fixture) => {
  if (!fixture) return '';
  return [
    fixture.id,
    fixture.status,
    fixture.time,
    fixture.home_goals,
    fixture.away_goals,
    fixture.fh_home_goals,
    fixture.fh_away_goals,
  ].join('|');
};

function App() {
  const [todayDate, setTodayDate] = useState(getRealtimeDate);
  const [selectedDate, setSelectedDate] = useState(getRealtimeDate);
  const [followToday, setFollowToday] = useState(true);
  const [fixtures, setFixtures] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [viewMode, setViewMode] = useState('predictions'); // 'predictions' | 'results'
  const [sport, setSport] = useState('football');           // 'football' | 'tennis'

  const [selectedFixtureId, setSelectedFixtureId] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState(null);
  const [isScanning, setIsScanning] = useState(false);
  const [coverage, setCoverage] = useState(null);
  const [liveClock, setLiveClock] = useState(Date.now());
  const selectedFixtureRealtimeKeyRef = useRef('');

  useEffect(() => {
    const updateClock = () => setTodayDate(getRealtimeDate());
    updateClock();
    const t = setInterval(updateClock, 60000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const tick = () => setLiveClock(Date.now());
    tick();
    const t = setInterval(tick, 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (followToday && selectedDate !== todayDate) {
      setSelectedDate(todayDate);
    }
  }, [followToday, selectedDate, todayDate]);

  const handleDateChange = useCallback((dateStr) => {
    setFollowToday(dateStr === todayDate);
    setSelectedDate(dateStr);
  }, [todayDate]);

  const handleMatchSelect = useCallback(async (fixture, isBackground = false) => {
    setSelectedFixtureId(fixture.id);
    if (!isBackground) {
      setAnalysis(null);
      setAnalysisLoading(true);
    }
    setAnalysisError(null);
    try {
      const res = await fetch(`${API}/analysis/match/${fixture.id}?home=${encodeURIComponent(fixture.home_team.name)}&away=${encodeURIComponent(fixture.away_team.name)}&league=${encodeURIComponent(fixture.league.name)}&live_home=${fixture.home_goals || 0}&live_away=${fixture.away_goals || 0}&status=${encodeURIComponent(fixture.status)}&start_time=${encodeURIComponent(fixture.time)}`);
      if (!res.ok) throw new Error("Analysis failed");
      const data = await res.json();
      // Merge fixture display info
      data.match = {
        ...data.match,
        home_team_logo: fixture.home_team.logo,
        away_team_logo: fixture.away_team.logo,
        home_team: fixture.home_team.name,
        away_team: fixture.away_team.name,
        league_name: fixture.league.name,
        league_logo: fixture.league.logo,
        time: fixture.time,
      };
      setAnalysis(data);
    } catch (e) {
      setAnalysisError(e.message);
    } finally {
      if (!isBackground) setAnalysisLoading(false);
    }
  }, []);

  // Fetch fixtures for the selected date
  const fetchFixtures = useCallback(async (dateStr, options = {}) => {
    const { isBackground = false, forceRefresh = false } = options;
    if (!isBackground) {
      setLoading(true);
      setError(null);
    }
    try {
      const params = new URLSearchParams();
      if (forceRefresh) params.set('force_refresh', 'true');
      if (isBackground || forceRefresh) params.set('t', String(Date.now()));
      const query = params.toString();
      const res = await fetch(`${API}/fixtures/${dateStr}${query ? `?${query}` : ''}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "Failed to fetch matches");
      }
      const data = await res.json();
      // Backend may return an array OR { fixtures: [], message: "..." } on failure
      const fixtureList = Array.isArray(data) ? data : (data.fixtures || []);
      const receivedAt = Date.now();
      const receivedFixtureList = fixtureList.map((fixture) => ({
        ...fixture,
        _client_received_at: receivedAt,
      }));
      const noDataMessage = !Array.isArray(data) && data.message ? data.message : null;

      setFixtures(receivedFixtureList);

      if (noDataMessage && !isBackground) {
        setError(noDataMessage);
      }

      // Auto-select first match if none selected
      if (!isBackground) {
        if (receivedFixtureList.length > 0) {
          // Wrap in setTimeout to avoid React state warning if handleMatchSelect triggers synchronously
          setTimeout(() => handleMatchSelect(receivedFixtureList[0]), 0);
        } else {
          setSelectedFixtureId(null);
          setAnalysis(null);
        }
      }

      // Pre-warm predictions in background for these fixtures
      if (receivedFixtureList.length > 0 && !isBackground) {
        fetch(`${API}/precompute-predictions?date_str=${dateStr}`).catch(console.error);
      }

      // Fetch coverage report
      fetch(`${API}/debug/coverage?date=${dateStr}`)
        .then(r => r.json())
        .then(data => setCoverage(data.coverage_score))
        .catch(console.error);

    } catch (e) {
      if (!isBackground) setError(e.message);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [handleMatchSelect]);

  useEffect(() => { fetchFixtures(selectedDate); }, [selectedDate, fetchFixtures]);

  // Calculate adaptive polling interval based on match status and tab visibility
  const [isTabVisible, setIsTabVisible] = useState(!document.hidden);

  useEffect(() => {
    const handleVisibilityChange = () => {
      const isVisible = !document.hidden;
      setIsTabVisible(isVisible);
      if (isVisible) {
        fetchFixtures(selectedDate, {
          isBackground: true,
          forceRefresh: selectedDate === todayDate,
        });
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [selectedDate, todayDate, fetchFixtures]);

  const pollInterval = React.useMemo(() => {
    if (!isTabVisible || selectedDate !== todayDate) return null;

    const statuses = fixtures.map(f => (f.status || '').toUpperCase());
    const hasLive = statuses.some(s => s === 'LIVE' || /^\d+$/.test(s) || s === '1H' || s === '2H');
    const hasHT = statuses.some(s => s === 'HT');
    const hasNS = statuses.some(s => s === 'NS' || s === 'TBD');

    if (hasLive) return 15000;  // 15 seconds
    if (hasHT) return 30000;    // 30 seconds
    if (hasNS) return 300000;   // 5 minutes
    
    return null; // FT -> never refresh
  }, [fixtures, isTabVisible, selectedDate, todayDate]);

  // Connect to WebSocket for real-time live scores
  useEffect(() => {
    // Only connect if we are viewing today's matches
    if (selectedDate !== todayDate) return;
    
    // Construct ws url from VITE_API_URL or window.location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = API.startsWith('http') 
      ? API.replace(/^http/, 'ws') + '/ws/live-scores'
      : `${protocol}//${window.location.host}${API}/ws/live-scores`;
      
    const ws = new WebSocket(wsUrl);
    
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "LIVE_SCORE_UPDATE") {
          setFixtures(prevFixtures => {
            const liveMatches = msg.data;
            let changed = false;
            const updatedFixtures = prevFixtures.map(f => {
              const liveData = liveMatches.find(lm => lm.event_id == f.id);
              if (liveData) {
                if (f.home_goals !== liveData.home_score || f.away_goals !== liveData.away_score || f.status !== liveData.status) {
                  changed = true;
                  return {
                    ...f,
                    home_goals: liveData.home_score,
                    away_goals: liveData.away_score,
                    status: liveData.status,
                  };
                }
              }
              return f;
            });
            return changed ? updatedFixtures : prevFixtures;
          });
        }
      } catch (e) {
        console.error("WebSocket message parse error:", e);
      }
    };
    
    return () => ws.close();
  }, [selectedDate, todayDate]);
  // Poll prediction statuses every few seconds if there are pending items
  useEffect(() => {
    if (!coverage || coverage.predicted_pending === 0) return;
    const t = setInterval(() => {
      fetch(`${API}/debug/coverage?date=${selectedDate}`)
        .then(r => r.json())
        .then(data => setCoverage(data.coverage_score))
        .catch(console.error);
    }, 5000);
    return () => clearInterval(t);
  }, [coverage, selectedDate]);

  const realtimeFixtures = React.useMemo(
    () => fixtures.map((fixture) => withRealtimeStatus(fixture, liveClock)),
    [fixtures, liveClock]
  );
  const selectedFixture = realtimeFixtures.find(f => f.id === selectedFixtureId);
  const selectedFixtureKey = fixtureRealtimeKey(selectedFixture);

  useEffect(() => {
    if (!selectedFixture || !analysis) {
      selectedFixtureRealtimeKeyRef.current = selectedFixtureKey;
      return;
    }
    if (
      selectedFixtureRealtimeKeyRef.current &&
      selectedFixtureRealtimeKeyRef.current !== selectedFixtureKey
    ) {
      handleMatchSelect(selectedFixture, true);
    }
    selectedFixtureRealtimeKeyRef.current = selectedFixtureKey;
  }, [analysis, handleMatchSelect, selectedFixture, selectedFixtureKey]);

  // If in results mode, show the full-screen results tracker
  if (viewMode === 'results') {
    return (
      <div className="h-screen flex flex-col overflow-hidden">
        <header className="shrink-0 h-14 bg-surface-1 border-b border-border flex items-center px-5 gap-3 z-30">
          <Trophy className="w-5 h-5 text-gold-500" />
          <span className="text-base font-bold tracking-widest text-white">
            FOOTBALL<span className="text-gold-500">PREDICT</span>
          </span>
          <div className="flex-1" />
          <button
            onClick={() => setViewMode('predictions')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-white hover:border-white/20 transition-all text-xs"
          >
            <Calendar className="w-3.5 h-3.5" />
            Predictions
          </button>
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-400 text-xs font-bold"
          >
            <Target className="w-3.5 h-3.5" />
            Results
          </button>
        </header>
        <div className="flex-1 overflow-hidden bg-surface-0">
          {sport === 'tennis' ? (
            <TennisResultsTracker
              onBack={() => setViewMode('predictions')}
              selectedDate={selectedDate}
            />
          ) : (
            <ResultsTracker
              onBack={() => setViewMode('predictions')}
              selectedDate={selectedDate}
            />
          )}
        </div>
      </div>
    );
  }

  const handleScanLiveOdds = async () => {
    if (isScanning) return;
    setIsScanning(true);
    try {
      const res = await fetch(`${API}/live/scan`);
      if (!res.ok) throw new Error("Live scan failed");
      const data = await res.json();
      alert(`Scan Complete!\nMatches Scanned: ${data.matches_scanned}\nSnapshots Saved: ${data.odds_snapshots_saved}\nExecutable Bets: ${data.executable_bets.length}`);
      console.log("Executable Bets:", data.executable_bets);
      console.log("Rejections:", data.rejections);
    } catch (e) {
      alert(`Scan Error: ${e.message}`);
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* ── Top Bar ─────────────────────────────────────── */}
      <header className="shrink-0 h-14 bg-surface-1 border-b border-border flex items-center px-5 gap-3 z-30">
        <Trophy className="w-5 h-5 text-gold-500" />
        <span className="text-base font-bold tracking-widest text-white">
          FOOTBALL<span className="text-gold-500">PREDICT</span>
        </span>

        {/* Sport Tabs */}
        <div className="flex items-center gap-1 ml-4 bg-white/5 rounded-lg p-0.5">
          <button
            id="tab-football"
            onClick={() => setSport('football')}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-bold transition-all ${
              sport === 'football'
                ? 'bg-gold-500/20 text-gold-400 border border-gold-500/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            ⚽ Football
          </button>
          <button
            id="tab-tennis"
            onClick={() => setSport('tennis')}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-bold transition-all ${
              sport === 'tennis'
                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            🎾 Tennis
          </button>
        </div>

        <div className="flex-1" />
        <button
          onClick={handleScanLiveOdds}
          disabled={isScanning || sport === 'tennis'}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-400 hover:bg-blue-500/25 transition-all text-xs font-bold disabled:opacity-50"
        >
          {isScanning ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Target className="w-3.5 h-3.5" />
          )}
          {isScanning ? "Scanning..." : "Scan Live Odds"}
        </button>
        <button
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gold-500/15 border border-gold-500/30 text-gold-500 text-xs font-bold"
        >
          <Calendar className="w-3.5 h-3.5" />
          Predictions
        </button>
        <button
          onClick={() => setViewMode('results')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-amber-400 hover:border-amber-500/30 transition-all text-xs"
        >
          <Target className="w-3.5 h-3.5" />
          Results
        </button>
      </header>

      {/* ── Date Picker ──────────────────────────────────── */}
      <DatePicker selectedDate={selectedDate} onDateChange={handleDateChange} today={todayDate} />

      {/* ── Tennis View (renders instead of football layout) ─── */}
      {sport === 'tennis' ? (
        <TennisView
          selectedDate={selectedDate}
          onDateChange={handleDateChange}
          todayDate={todayDate}
        />
      ) : (
        <>
          {/* ── Coverage Banner ──────────────────────────────── */}
          {coverage && (
            <div className="shrink-0 bg-surface-1 border-b border-border py-1.5 px-4 flex justify-center gap-6 text-[10px] uppercase tracking-wider text-slate-400 font-bold">
              <div className="flex gap-1.5">
                <span>Provider:</span>
                <span className="text-white">{coverage.provider_date_matched}</span>
              </div>
              <div className="flex gap-1.5">
                <span>Stored:</span>
                <span className="text-white">{coverage.stored_count}</span>
              </div>
              <div className="flex gap-1.5">
                <span>Rendered:</span>
                <span className="text-white">{coverage.rendered_count}</span>
              </div>
              <div className="flex gap-1.5">
                <span>Predicted:</span>
                <span className={coverage.predicted_ready === coverage.stored_count ? "text-green-400" : "text-amber-400"}>
                  {coverage.predicted_ready} / {coverage.stored_count}
                </span>
              </div>
              <div className="flex gap-1.5 border-l border-white/10 pl-6">
                <span>Coverage:</span>
                <span className={coverage.coverage_pct === 100 ? "text-green-400" : "text-amber-400"}>
                  {coverage.coverage_pct}%
                </span>
              </div>
            </div>
          )}

          {/* ── Main 2-Panel Layout ──────────────────────────── */}
          <div className="flex-1 flex overflow-hidden">
            {/* Left Panel — Match List grouped by league */}
            <aside className="w-[380px] shrink-0 bg-surface-1 border-r border-border overflow-y-auto">
              {loading ? (
                <div className="flex flex-col items-center justify-center py-20 text-slate-500">
                  <Loader2 className="w-6 h-6 animate-spin text-gold-500 mb-3" />
                  <span className="text-xs tracking-widest uppercase">Loading Matches</span>
                </div>
              ) : error && realtimeFixtures.length === 0 ? (
                <div className="p-6 text-center text-slate-500 text-sm">
                  <AlertCircle className="w-6 h-6 mx-auto mb-2 opacity-30" />
                  {error}
                </div>
              ) : realtimeFixtures.length === 0 ? (
                <div className="p-8 text-center text-slate-500 text-sm">
                  No matches found for this date.
                </div>
              ) : (() => {
                // Group fixtures by time
                const groups = [];
                const seen = {};
                for (const match of realtimeFixtures) {
                  const t = match.time || "TBD";
                  if (!seen[t]) {
                    seen[t] = { time: t, matches: [] };
                    groups.push(seen[t]);
                  }
                  seen[t].matches.push(match);
                }
                return (
                  <div className="py-1">
                    {/* Total count header */}
                    <div className="px-4 py-2 flex items-center gap-2 border-b border-white/5">
                      <span className="text-[10px] text-slate-500 uppercase tracking-widest">All Matches</span>
                      <span className="ml-auto text-[10px] font-bold text-gold-500 bg-gold-500/10 px-2 py-0.5 rounded-full">
                        {realtimeFixtures.length}
                      </span>
                    </div>

                    {groups.map((group) => (
                      <div key={group.time}>
                        {/* Time header */}
                        <div className="sticky top-0 z-10 flex items-center gap-2 px-3 py-1.5 bg-surface-1/95 backdrop-blur border-b border-white/[0.04] border-t border-t-white/[0.04]">
                          <div className="flex-1 min-w-0">
                            <span className="text-[11px] font-bold text-white tracking-widest truncate block leading-tight">
                              {group.time}
                            </span>
                          </div>
                          <span className="text-[9px] text-slate-500 shrink-0">{group.matches.length} matches</span>
                        </div>

                        {/* Matches at this time */}
                        {group.matches.map((match) => (
                          <MatchRow
                            key={match.id}
                            match={match}
                            isSelected={match.id === selectedFixtureId}
                            onClick={() => handleMatchSelect(match)}
                            selectedDate={selectedDate}
                            todayDate={todayDate}
                          />
                        ))}
                      </div>
                    ))}
                  </div>
                );
              })()}
            </aside>

            {/* Center Panel — Match Detail / Analysis */}
            <main className="flex-1 overflow-y-auto bg-surface-0">
              {selectedFixture ? (
                <MatchDetail
                  fixture={selectedFixture}
                  analysis={analysis}
                  loading={analysisLoading}
                  error={analysisError}
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-600">
                  <Trophy className="w-12 h-12 mb-4 opacity-30" />
                  <p className="text-sm">Select a match to view analysis</p>
                </div>
              )}
            </main>
          </div>
        </>
      )}
    </div>
  );
}

export default App;
