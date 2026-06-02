import React, { useState, useCallback } from 'react';
import { Trophy } from 'lucide-react';
import DatePicker from './components/DatePicker';
import DailyMatches from './components/DailyMatches';
import DailyResults from './components/DailyResults';

const pad = (n) => String(n).padStart(2, '0');
const fmtDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function App() {
  const [selectedDate, setSelectedDate] = useState(fmtDate(new Date()));
  const [activeTab, setActiveTab] = useState('matches'); // 'matches' | 'results'

  const handleFallbackDate = useCallback((fallbackDate) => {
    setSelectedDate(fallbackDate);
  }, []);



  return (
    <div className="h-screen flex flex-col overflow-hidden">
      {/* ── Top Bar ─────────────────────────────────────── */}
      <header className="shrink-0 h-14 bg-surface-1 border-b border-border flex items-center px-5 gap-3 z-30">
        <Trophy className="w-5 h-5 text-gold-500" />
        <span className="text-base font-bold tracking-widest text-white">
          FOOTBALL<span className="text-gold-500">PREDICT</span>
        </span>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          <button onClick={() => setActiveTab('matches')} className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${activeTab === 'matches' ? 'bg-white/10 text-white' : 'bg-transparent text-slate-300'}`}>Matches</button>
          <button onClick={() => setActiveTab('results')} className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${activeTab === 'results' ? 'bg-white/10 text-white' : 'bg-transparent text-slate-300'}`}>Results</button>
        </div>
      </header>

      {/* ── Date Picker ──────────────────────────────────── */}
      <DatePicker selectedDate={selectedDate} onDateChange={setSelectedDate} />

      {/* ── Main 2-Panel Layout ──────────────────────────── */}
      <div className="flex-1 flex overflow-hidden">
        <aside className="w-[380px] shrink-0 bg-surface-1 border-r border-border overflow-y-auto">
          {/* Left panel reserved for match list; content loaded by DailyMatches component */}
        </aside>

        {/* Center Panel — Daily Tabs Content */}
        <main className="flex-1 overflow-y-auto bg-surface-0">
          {activeTab === 'matches' && (
            <DailyMatches
              selectedDate={selectedDate}
              onFallbackDate={handleFallbackDate}
            />
          )}
          {activeTab === 'results' && <DailyResults />}
        </main>
      </div>
    </div>
  );
}

export default App;
