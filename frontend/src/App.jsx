import React, { useState, useEffect, useCallback } from 'react';
import { Trophy, Loader2, AlertCircle, Calendar, Target } from 'lucide-react';
import MatchRow from './components/MatchRow';
import MatchDetail from './components/MatchDetail';
import DatePicker from './components/DatePicker';
import ResultsTracker from './components/ResultsTracker';
import { API, fixLogo } from './config';

const pad = (n) => String(n).padStart(2, '0');
const fmtDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function App() {
  const [selectedDate, setSelectedDate] = useState(fmtDate(new Date()));
  const [fixtures, setFixtures] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [viewMode, setViewMode] = useState('predictions'); // 'predictions' | 'results'

  const [selectedFixtureId, setSelectedFixtureId] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState(null);
  const [isScanning, setIsScanning] = useState(false);

  // Fetch fixtures for the selected date
  const fetchFixtures = useCallback(async (dateStr) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/fixtures/${dateStr}`);
      if (!res.ok) throw new Error("Failed to fetch matches");
      const data = await res.json();

      // Fix relative logo URLs to absolute
      data.forEach(f => {
        if (f.league) f.league.logo = fixLogo(f.league.logo);
        if (f.home_team) f.home_team.logo = fixLogo(f.home_team.logo);
        if (f.away_team) f.away_team.logo = fixLogo(f.away_team.logo);
      });

      setFixtures(data);
      // Auto-select first match if none selected
      if (data.length > 0) {
        handleMatchSelect(data[0]);
      } else {
        setSelectedFixtureId(null);
        setAnalysis(null);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchFixtures(selectedDate); }, [selectedDate, fetchFixtures]);

  const handleMatchSelect = async (fixture) => {
    setSelectedFixtureId(fixture.id);
    setAnalysis(null);
    setAnalysisError(null);
    setAnalysisLoading(true);
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
      setAnalysisLoading(false);
    }
  };

  // Group fixtures by league
  const grouped = fixtures.reduce((acc, f) => {
    const key = f.league.name;
    if (!acc[key]) acc[key] = { league: f.league, matches: [] };
    acc[key].matches.push(f);
    return acc;
  }, {});

  const selectedFixture = fixtures.find(f => f.id === selectedFixtureId);

  // If in results mode, show the full-screen results tracker
  if (viewMode === 'results') {
    return (
      <div className="h-screen flex flex-col overflow-hidden min-h-0">
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
        <div className="flex-1 min-h-0 overflow-hidden bg-surface-0">
          <ResultsTracker
            onBack={() => setViewMode('predictions')}
            selectedDate={selectedDate}
          />
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
    <div className="h-screen flex flex-col overflow-hidden min-h-0">
      {/* ── Top Bar ─────────────────────────────────────── */}
      <header className="shrink-0 h-14 bg-surface-1 border-b border-border flex items-center px-5 gap-3 z-30">
        <Trophy className="w-5 h-5 text-gold-500" />
        <span className="text-base font-bold tracking-widest text-white">
          FOOTBALL<span className="text-gold-500">PREDICT</span>
        </span>
        <div className="flex-1" />
        <button
          onClick={handleScanLiveOdds}
          disabled={isScanning}
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
      <DatePicker selectedDate={selectedDate} onDateChange={setSelectedDate} />

      {/* ── Main 2-Panel Layout ──────────────────────────── */}
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {/* Left Panel — Match List */}
        <aside className="w-[380px] shrink-0 bg-surface-1 border-r border-border overflow-y-auto min-h-0">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 text-slate-500">
              <Loader2 className="w-6 h-6 animate-spin text-gold-500 mb-3" />
              <span className="text-xs tracking-widest uppercase">Loading Matches</span>
            </div>
          ) : error ? (
            <div className="p-6 text-center text-red-400 text-sm">
              <AlertCircle className="w-6 h-6 mx-auto mb-2 opacity-50" />
              {error}
            </div>
          ) : fixtures.length === 0 ? (
            <div className="p-8 text-center text-slate-500 text-sm">
              No matches found for this date.
            </div>
          ) : (
            <div className="py-2">
              {fixtures.map((match, idx) => (
                <div key={match.id} className="animate-fade-in" style={{ animationDelay: `${idx * 0.02}s` }}>
                  <MatchRow
                    match={match}
                    isSelected={match.id === selectedFixtureId}
                    onClick={() => handleMatchSelect(match)}
                  />
                </div>
              ))}
            </div>
          )}
        </aside>

        {/* Center Panel — Match Detail / Analysis */}
        <main className="flex-1 min-h-0 overflow-y-auto bg-surface-0">
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
    </div>
  );
}

export default App;
