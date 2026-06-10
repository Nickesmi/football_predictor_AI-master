import React from 'react';
import { ChevronLeft, ChevronRight, Calendar } from 'lucide-react';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const pad = (n) => String(n).padStart(2, '0');
const fmtDate = (d) => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;

const DatePicker = ({ selectedDate, onDateChange }) => {
  const today = fmtDate(new Date());
  const sel = new Date(selectedDate + 'T00:00:00');

  const shift = (days) => {
    const d = new Date(sel);
    d.setDate(d.getDate() + days);
    onDateChange(fmtDate(d));
  };

  // Generate 7 days centered on selection
  const dates = [];
  for (let i = -3; i <= 3; i++) {
    const d = new Date(sel);
    d.setDate(d.getDate() + i);
    dates.push(d);
  }

  const isToday = (d) => fmtDate(d) === today;
  const isSelected = (d) => fmtDate(d) === selectedDate;

  const label = (d) => {
    if (isToday(d)) return 'Today';
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    if (fmtDate(d) === fmtDate(yesterday)) return 'Yesterday';
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    if (fmtDate(d) === fmtDate(tomorrow)) return 'Tomorrow';
    return `${DAYS[d.getDay()]}`;
  };

  return (
    <div className="shrink-0 bg-surface-1 border-b border-border">
      <div className="flex items-center justify-center gap-1 py-2.5 px-4">
        <button
          onClick={() => shift(-1)}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>

        <div className="flex items-center gap-0.5">
          {dates.map(d => {
            const key = fmtDate(d);
            const selected = isSelected(d);
            return (
              <button
                key={key}
                onClick={() => onDateChange(key)}
                className={`flex flex-col items-center px-4 py-1.5 rounded-lg text-center transition-all duration-200 min-w-[72px] ${
                  selected
                    ? 'bg-gold-500 text-surface-0 shadow-[0_0_15px_rgba(234,179,8,0.25)]'
                    : isToday(d)
                      ? 'text-gold-500 hover:bg-white/5'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
                }`}
              >
                <span className={`text-[10px] font-bold uppercase tracking-wider ${selected ? 'text-surface-0/80' : ''}`}>
                  {label(d)}
                </span>
                <span className={`text-sm font-semibold ${selected ? '' : ''}`}>
                  {d.getDate()} {MONTHS[d.getMonth()]}
                </span>
              </button>
            );
          })}
        </div>

        <button
          onClick={() => shift(1)}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
        >
          <ChevronRight className="w-4 h-4" />
        </button>

        <div className="h-4 w-px bg-white/10 mx-1" />

        <div className="relative w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/5 transition-colors cursor-pointer" title="Select custom date">
          <Calendar className="w-4 h-4" />
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => {
              if (e.target.value) {
                onDateChange(e.target.value);
              }
            }}
            className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
          />
        </div>
      </div>
    </div>
  );
};

export default DatePicker;
