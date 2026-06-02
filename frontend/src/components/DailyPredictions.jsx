import React, { useEffect, useState } from 'react';

const API = "http://127.0.0.1:8001/api";

export default function DailyPredictions() {
    const [preds, setPreds] = useState([]);
    const [loading, setLoading] = useState(true);
    const [generating, setGenerating] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const res = await fetch(`${API}/daily/predictions`);
            const data = await res.json();
            setPreds(data.predictions || []);
        } catch (e) { console.error(e); } finally { setLoading(false) }
    };

    useEffect(() => { load(); }, []);

    const handleGenerate = async () => {
        setGenerating(true);
        try {
            await fetch(`${API}/daily/predict`, { method: 'POST' });
            await load();
        } catch (e) { console.error(e); } finally { setGenerating(false) }
    };

    if (loading) return <div className="p-4 text-sm text-slate-400">Loading predictions...</div>;
    return (
        <div className="p-4">
            <div className="mb-3 flex items-center gap-3">
                <button onClick={handleGenerate} disabled={generating} className="px-3 py-2 rounded-lg bg-amber-500 text-slate-900 text-xs font-semibold">{generating ? 'Generating...' : 'Generate Predictions'}</button>
            </div>
            {preds.length === 0 ? (
                <div className="text-sm text-slate-400">No predictions yet. Click Generate Predictions.</div>
            ) : (
                <div className="space-y-2">
                    {preds.map((p) => (
                        <div key={p.match_id} className="p-3 bg-surface-2 rounded-lg border border-white/6">
                            <div className="text-sm font-semibold">{p.match_id}</div>
                            <div className="text-xs text-slate-400">Generated at: {p.generated_at}</div>
                            <div className="mt-2 text-sm">
                                <div>Home Win: {p.predictions.home_win ?? '-'}%</div>
                                <div>Draw: {p.predictions.draw ?? '-'}%</div>
                                <div>Away Win: {p.predictions.away_win ?? '-'}%</div>
                                <div>BTTS: {p.predictions.btts ?? '-'}%</div>
                                <div>Over 2.5: {p.predictions.over_2_5 ?? '-'}%</div>
                                <div>Exp Goals: {p.predictions.expected_goals ? `${p.predictions.expected_goals.home}/${p.predictions.expected_goals.away}` : '-'}</div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
