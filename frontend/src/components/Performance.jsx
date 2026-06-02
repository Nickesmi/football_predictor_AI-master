import React, { useEffect, useState } from 'react';
const API = "http://127.0.0.1:8001/api";

export default function Performance() {
    const [perf, setPerf] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let mounted = true;
        async function load() {
            setLoading(true);
            try {
                const res = await fetch(`${API}/daily/performance`);
                const data = await res.json();
                if (!mounted) return;
                setPerf(data);
            } catch (e) { console.error(e); } finally { if (mounted) setLoading(false); }
        }
        load();
        return () => mounted = false;
    }, []);

    if (loading) return <div className="p-4 text-sm text-slate-400">Loading performance...</div>;
    if (!perf) return <div className="p-4 text-sm text-slate-400">No performance data.</div>;

    return (
        <div className="p-4 space-y-4">
            <div className="bg-surface-2 p-3 rounded-lg">
                <div className="text-sm font-semibold">Overall</div>
                <div className="text-xs text-slate-400">Total Predictions: {perf.total_predictions}</div>
                <div className="text-xs text-slate-400">Total Settled: {perf.total_settled}</div>
                <div className="text-xs text-slate-400">Overall Accuracy: {perf.overall_accuracy}%</div>
            </div>

            <div className="bg-surface-2 p-3 rounded-lg">
                <div className="text-sm font-semibold">By Market</div>
                <div className="mt-2 space-y-2 text-sm">
                    {Object.keys(perf.markets || {}).map(k => (
                        <div key={k} className="flex justify-between">
                            <div className="capitalize">{k.replace('_', ' ')} ({perf.markets[k].count})</div>
                            <div className="text-slate-200">Acc: {perf.markets[k].accuracy}% • ROI: {perf.markets[k].roi ?? 'N/A'}%</div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
