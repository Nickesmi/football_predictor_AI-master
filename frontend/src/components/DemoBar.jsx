import React from 'react';
import { Database, RefreshCw, Loader2, Info } from 'lucide-react';
import { dataBadge, refreshControl } from '../demo/demoRefresh';

// Demo-mode strip: what the list shows (cached snapshot and its age), the authorized allowance,
// and the only control that can spend an API-Football request.
export default function DemoBar({ status, cache, busy, notice, onRefresh }) {
  const control = refreshControl(status, cache, { busy });
  if (!control.visible) return null;
  const badge = dataBadge(cache);
  return (
    <div className="shrink-0 bg-surface-1 border-b border-border px-4 py-2 flex flex-wrap items-center gap-3 text-[11px]">
      <span className="px-2 py-0.5 rounded bg-amber-500/15 border border-amber-500/30 text-amber-400 font-bold tracking-widest uppercase">
        Demo
      </span>
      {badge && (
        <span className={`flex items-center gap-1.5 ${badge.kind === 'cached' ? 'text-slate-300' : 'text-slate-500'}`}>
          <Database className="w-3.5 h-3.5" />
          {badge.text}
        </span>
      )}
      <div className="flex-1" />
      <span className="text-slate-500">{control.detail}</span>
      <button
        onClick={onRefresh}
        disabled={!control.enabled}
        title={control.reason || `Uses ${control.cost} API-Football request`}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gold-500/10 border border-gold-500/25 text-gold-500 hover:bg-gold-500/20 transition-all font-bold disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
        {control.label}
      </button>
      {(control.reason || notice) && (
        <div className="basis-full flex items-center gap-1.5 text-slate-500">
          <Info className="w-3.5 h-3.5 shrink-0" />
          <span>{notice || control.reason}</span>
        </div>
      )}
    </div>
  );
}
