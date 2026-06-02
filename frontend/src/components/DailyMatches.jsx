import React, { useEffect, useState } from 'react';

const API = "http://127.0.0.1:8001/api";

function hasPrediction(pred) {
    if (!pred || typeof pred !== 'object') return false;
    return (
        pred.predicted_result
        || pred.home_win != null
        || pred.home_win_pct != null
    );
}

function formatPrediction(pred) {
    const homePct = Math.round(pred.home_win_pct ?? pred.home_win ?? 0);
    const drawPct = Math.round(pred.draw_pct ?? pred.draw ?? 0);
    const awayPct = Math.round(pred.away_win_pct ?? pred.away_win ?? 0);
    const result = pred.predicted_result || '—';
    let score = pred.predicted_score;
    if (!score) {
        const exp = pred.expected_goals || {};
        if (exp.home != null && exp.away != null) {
            score = `${parseFloat(exp.home).toFixed(1)} - ${parseFloat(exp.away).toFixed(1)}`;
        }
    }
    return { result, score: score || '—', homePct, drawPct, awayPct };
}

export default function DailyMatches({ selectedDate, onFallbackDate }) {
    const [matches, setMatches] = useState([]);
    const [displayDate, setDisplayDate] = useState(selectedDate);
    const [loading, setLoading] = useState(true);
    const [generating, setGenerating] = useState(false);

    useEffect(() => {
        let mounted = true;

        async function loadMatches(dateStr) {
            const params = new URLSearchParams({ match_date: dateStr, refresh: 'true' });
            const res = await fetch(`${API}/daily/matches?${params}`);
            return res.json();
        }

        async function load() {
            setLoading(true);
            try {
                let mJson = await loadMatches(selectedDate);
                if (!mounted) return;

                const effectiveDate = mJson.fallback_date || mJson.date || selectedDate;
                let matchList = mJson.matches || [];

                const missing = matchList.some((m) => !hasPrediction(m.prediction));
                if (missing && matchList.length > 0) {
                    setGenerating(true);
                    const predictParams = new URLSearchParams({ match_date: effectiveDate });
                    await fetch(`${API}/daily/predict?${predictParams}`, { method: 'POST' });
                    mJson = await loadMatches(effectiveDate);
                    if (!mounted) return;
                    matchList = mJson.matches || [];
                }

                setDisplayDate(mJson.fallback_date || mJson.date || selectedDate);
                setMatches(matchList);

                if (mJson.fallback_date && onFallbackDate) {
                    onFallbackDate(mJson.fallback_date);
                }
            } catch (e) {
                console.error(e);
                if (mounted) setMatches([]);
            } finally {
                if (mounted) {
                    setLoading(false);
                    setGenerating(false);
                }
            }
        }

        load();
        return () => { mounted = false; };
    }, [selectedDate, onFallbackDate]);

    if (loading) {
        return (
            <div className="p-4 text-sm text-slate-400">
                {generating ? 'Generating predictions...' : 'Loading matches...'}
            </div>
        );
    }

    if (!matches.length) {
        return (
            <div className="p-4 text-sm text-slate-400">
                No matches found for {selectedDate}.
            </div>
        );
    }

    const usingFallback = displayDate !== selectedDate;

    return (
        <div className="p-4 space-y-3">
            {usingFallback && (
                <p className="text-xs text-amber-400/90">
                    No matches today — showing {displayDate} ({matches.length} matches).
                </p>
            )}
            {matches.map((m) => {
                const pred = m.prediction || {};
                const ready = hasPrediction(pred);
                const { result, score, homePct, drawPct, awayPct } = formatPrediction(pred);

                return (
                    <div key={m.match_id} className="p-3 bg-surface-2 rounded-lg border border-white/6">
                        <div className="flex items-center justify-between">
                            <div>
                                <div className="text-sm font-semibold">{m.home_team} vs {m.away_team}</div>
                                <div className="text-xs text-slate-400">{m.league} • {m.kickoff || 'TBD'}</div>
                            </div>
                            <div className="text-sm text-slate-200 text-right">
                                <div className="font-medium">
                                    Predicted: {ready ? result : 'Generating...'}
                                </div>
                                {ready && (
                                    <>
                                        <div className="text-xs text-slate-400">{score}</div>
                                        <div className="text-xs">
                                            Home: {homePct}% • Draw: {drawPct}% • Away: {awayPct}%
                                        </div>
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
