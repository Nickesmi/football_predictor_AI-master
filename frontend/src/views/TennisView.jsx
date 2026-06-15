import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, AlertCircle } from 'lucide-react';
import TennisMatchRow from '../components/TennisMatchRow';
import TennisPredictionCard from '../components/TennisPredictionCard';
import DatePicker from '../components/DatePicker';

const API = import.meta.env.VITE_API_URL || '/api';

const TennisView = ({ selectedDate, onDateChange, todayDate }) => {
  const [matches, setMatches]         = useState([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [selectedMatch, setSelectedMatch] = useState(null);
  const [prediction, setPrediction]   = useState(null);
  const [predLoading, setPredLoading] = useState(false);
  const [predError, setPredError]     = useState(null);
  const [baseline, setBaseline]       = useState(null);

  // ── Fetch matches ──────────────────────────────────────────────────────────
  const fetchMatches = useCallback(async (date) => {
    setLoading(true);
    setError(null);
    try {
      const res  = await fetch(`${API}/tennis/matches?date=${date}`);
      if (!res.ok) throw new Error('Failed to fetch tennis matches');
      const data = await res.json();
      setMatches(data.matches || []);
      if (!data.matches?.length && data.message) setError(data.message);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Fetch prediction ───────────────────────────────────────────────────────
  const fetchPrediction = useCallback(async (match) => {
    setSelectedMatch(match);
    setPrediction(null);
    setPredLoading(true);
    setPredError(null);
    try {
      const res  = await fetch(`${API}/tennis/predict/${match.match_id}`);
      if (!res.ok) throw new Error('Prediction unavailable');
      const data = await res.json();
      setPrediction(data);
    } catch (e) {
      setPredError(e.message);
    } finally {
      setPredLoading(false);
    }
  }, []);

  // ── Fetch baseline analytics ───────────────────────────────────────────────
  const fetchBaseline = useCallback(async () => {
    try {
      const res  = await fetch(`${API}/tennis/analytics/baseline`);
      const data = await res.json();
      setBaseline(data);
    } catch (_) {}
  }, []);

  useEffect(() => { fetchMatches(selectedDate); }, [selectedDate, fetchMatches]);
  useEffect(() => { fetchBaseline(); }, [fetchBaseline]);

  // Group matches by tournament
  const grouped = matches.reduce((acc, m) => {
    const key = m.tournament || 'Unknown Tournament';
    if (!acc[key]) acc[key] = [];
    acc[key].push(m);
    return acc;
  }, {});

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* ── Left Panel — Match List ──────────────────────────────────────── */}
      <aside className="w-[380px] shrink-0 bg-surface-1 border-r border-border overflow-y-auto">
        {/* Governance Banner */}
        {baseline && (
          <div className="px-4 py-2 bg-[#0d1117] border-b border-white/5">
            <div className="flex items-center justify-between text-[9px] uppercase tracking-wider">
              <span className="text-slate-500">Settled Picks</span>
              <span className={`font-bold ${
                baseline.settled_predictions >= baseline.calibration_threshold
                  ? 'text-emerald-400' : 'text-amber-400'
              }`}>
                {baseline.settled_predictions} / {baseline.calibration_threshold}
              </span>
            </div>
            <div className="mt-1 h-0.5 bg-white/5 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500/60 rounded-full"
                style={{
                  width: `${Math.min(100, (baseline.settled_predictions / baseline.calibration_threshold) * 100)}%`
                }}
              />
            </div>
            <div className="mt-1 text-[8px] text-slate-600 uppercase tracking-wider">
              Calibration: {baseline.calibration_status}
            </div>
          </div>
        )}

        {/* Match Count */}
        <div className="px-4 py-2 flex items-center gap-2 border-b border-white/5">
          <span className="text-[10px] text-slate-500 uppercase tracking-widest">Tennis Matches</span>
          <span className="ml-auto text-[10px] font-bold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full">
            {matches.length}
          </span>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-500">
            <Loader2 className="w-6 h-6 animate-spin text-emerald-500 mb-3" />
            <span className="text-xs tracking-widest uppercase">Loading Tennis</span>
          </div>
        ) : error && matches.length === 0 ? (
          <div className="p-6 text-center text-slate-500 text-sm">
            <AlertCircle className="w-6 h-6 mx-auto mb-2 opacity-30" />
            {error}
          </div>
        ) : matches.length === 0 ? (
          <div className="p-8 text-center text-slate-500 text-sm">
            No tennis matches found for this date.
          </div>
        ) : (
          <div className="py-1">
            {Object.entries(grouped).map(([tournament, tournamentMatches]) => (
              <div key={tournament}>
                {/* Tournament header */}
                <div className="sticky top-0 z-10 flex items-center gap-2 px-3 py-1.5 bg-surface-1/95 backdrop-blur border-b border-white/[0.04] border-t border-t-white/[0.04]">
                  <span className="text-[11px] font-bold text-white tracking-wide truncate flex-1">
                    {tournament}
                  </span>
                  <span className="text-[9px] text-slate-500 shrink-0">{tournamentMatches.length}</span>
                </div>
                {tournamentMatches.map((match) => (
                  <TennisMatchRow
                    key={match.match_id}
                    match={match}
                    isSelected={selectedMatch?.match_id === match.match_id}
                    onClick={() => fetchPrediction(match)}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
      </aside>

      {/* ── Right Panel — Prediction Detail ─────────────────────────────── */}
      <main className="flex-1 overflow-y-auto bg-surface-0">
        {selectedMatch ? (
          <div className="max-w-2xl mx-auto p-4 sm:p-6 animate-fade-in">
            {/* Match Header */}
            <div className="bg-[#111318] border border-white/5 rounded-2xl p-6 mb-4 shadow-2xl">
              <div className="flex flex-col items-center gap-4">
                {/* Surface + Tournament */}
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border ${
                    selectedMatch.surface === 'clay'  ? 'bg-orange-500/20 text-orange-300 border-orange-500/30' :
                    selectedMatch.surface === 'grass' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' :
                    'bg-blue-500/20 text-blue-300 border-blue-500/30'
                  }`}>
                    {selectedMatch.surface || 'hard'}
                  </span>
                  <span className="text-[10px] text-slate-400 uppercase tracking-wider">
                    {selectedMatch.tournament}
                  </span>
                </div>

                {/* Players vs Score */}
                <div className="flex items-center gap-6 w-full justify-center">
                  <div className="flex-1 text-center">
                    <div className="text-lg font-bold text-white">{selectedMatch.player_1}</div>
                    {selectedMatch.rank_1 && (
                      <div className="text-[10px] text-slate-500">ATP #{selectedMatch.rank_1}</div>
                    )}
                  </div>
                  <div className="text-center px-4">
                    {(selectedMatch.sets_1 > 0 || selectedMatch.sets_2 > 0) ? (
                      <div className="text-2xl font-bold text-white tabular-nums">
                        {selectedMatch.sets_1} – {selectedMatch.sets_2}
                      </div>
                    ) : (
                      <div className="text-[10px] text-emerald-400 uppercase tracking-[0.2em] font-bold">
                        {selectedMatch.status}
                      </div>
                    )}
                    <div className="text-[9px] text-slate-500 mt-1">
                      {selectedMatch.start_time || 'TBD'}
                    </div>
                    {selectedMatch.is_stale && (
                      <div className="text-[9px] text-red-400 mt-0.5 flex items-center gap-0.5 justify-center">
                        <AlertCircle className="w-2.5 h-2.5" /> STALE
                      </div>
                    )}
                  </div>
                  <div className="flex-1 text-center">
                    <div className="text-lg font-bold text-white">{selectedMatch.player_2}</div>
                    {selectedMatch.rank_2 && (
                      <div className="text-[10px] text-slate-500">ATP #{selectedMatch.rank_2}</div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Prediction Card */}
            <TennisPredictionCard
              match={selectedMatch}
              prediction={prediction}
              loading={predLoading}
              error={predError}
            />
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-600">
            <div className="text-5xl mb-4">🎾</div>
            <p className="text-sm">Select a match to view prediction</p>
          </div>
        )}
      </main>
    </div>
  );
};

export default TennisView;
