import React, { useEffect, useState } from 'react';
const API = "http://127.0.0.1:8001/api";

export default function ProfitMode() {
    const [opps, setOpps] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let mounted = true;
        async function load() {
            setLoading(true);
            try {
                const res = await fetch(`${API}/daily/opportunities`);
                const data = await res.json();
                if (!mounted) return;
                setOpps(data.opportunities || []);
            } catch (e) { console.error(e); } finally { if (mounted) setLoading(false); }
        }
        load();
        return () => mounted = false;
    }, []);

    if (loading) return <div className="p-4 text-sm text-slate-400">Loading opportunities...</div>;
    if (!opps || opps.length === 0) return <div className="p-4 text-sm text-slate-400">No opportunities found.</div>;

    return (
        <div className="p-4 space-y-3">
            {opps.map(o => (
                <div key={o.match_id} className="p-3 bg-surface-2 rounded-lg border border-white/6">
                    <div className="text-sm font-semibold">{o.match_id}</div>
                    <div className="text-xs text-slate-400">Markets:</div>
                    <div className="mt-2 text-sm">
                        {o.markets.map(m => (
                            <div key={m.market} className="flex justify-between border-b border-white/6 py-1">
                                <div>{m.market}</div>
                                <div className="text-slate-200">edge {m.edge} • ev {m.ev} • odds {m.odds}</div>
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}
