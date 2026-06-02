import React, { useEffect, useState } from 'react';
const API = "http://127.0.0.1:8001/api";

export default function DailyResults() {
    const [results, setResults] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let mounted = true;
        async function load() {
            setLoading(true);
            try {
                const res = await fetch(`${API}/daily/results`);
                const data = await res.json();
                if (!mounted) return;
                setResults(data.results || []);
                // could surface summary if needed: data.summary
            } catch (e) { console.error(e); } finally { if (mounted) setLoading(false); }
        }
        load();
        return () => mounted = false;
    }, []);

    if (loading) return <div className="p-4 text-sm text-slate-400">Loading results...</div>;
    if (!results || results.length === 0) return <div className="p-4 text-sm text-slate-400">No results recorded for today.</div>;

    return (
        <div className="p-4 space-y-3">
            {results.map(r => {
                const pred = r.predictions || {};
                const homePct = pred.home_win ? Math.round(pred.home_win) : '-';
                const drawPct = pred.draw ? Math.round(pred.draw) : '-';
                const awayPct = pred.away_win ? Math.round(pred.away_win) : '-';
                const exp = pred.expected_goals || {};
                const predictedScore = exp.home != null && exp.away != null ? `${parseFloat(exp.home).toFixed(1)} - ${parseFloat(exp.away).toFixed(1)}` : '-';
                return (
                    <div key={r.match_id} className="p-3 bg-surface-2 rounded-lg border border-white/6">
                        <div className="flex justify-between items-center">
                            <div>
                                <div className="text-sm font-semibold">{r.match_id}</div>
                                <div className="text-xs text-slate-400">Predicted: {predictedScore} • Confidence: {Math.max(pred.home_win || 0, pred.draw || 0, pred.away_win || 0) || 0}%</div>
                            </div>
                            <div className="text-sm">{r.home_goals} - {r.away_goals} <span className={`ml-2 ${r.hit ? 'text-emerald-400' : 'text-red-400'}`}>{r.hit ? 'Correct' : 'Incorrect'}</span></div>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
