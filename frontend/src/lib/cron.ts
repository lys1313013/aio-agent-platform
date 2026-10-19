// Persisted numeric weekdays use legacy APScheduler numbering (Monday=0).
// New input is standard cron; convert stored values to names before editing.
const names = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const labels = ['一', '二', '三', '四', '五', '六', '日'];

export function storedCronForEditing(expr: string): string {
  const parts = expr.trim().toLowerCase().split(/\s+/);
  if (parts.length !== 5 || parts[4] === '*') return expr;
  try {
    const days = new Set<number>();
    const number = (v: string) => {
      const n = names.includes(v) ? names.indexOf(v) : /^\d+$/.test(v) ? Number(v) : NaN;
      if (!Number.isInteger(n) || n < 0 || n > 6) throw new Error('weekday');
      return n;
    };
    for (const item of parts[4].split(',')) {
      const [span, stepText, extra] = item.split('/');
      const step = stepText === undefined ? 1 : Number(stepText);
      if (extra !== undefined || !Number.isInteger(step) || step < 1) throw new Error('step');
      const range = span.split('-');
      if (range.length > 2) throw new Error('range');
      const start = span === '*' ? 0 : number(range[0]);
      const end = span === '*' || (stepText !== undefined && range.length === 1)
        ? 6 : range.length === 2 ? number(range[1]) : start;
      if (start > end) throw new Error('range');
      for (let n = start; n <= end; n += step) days.add(n);
    }
    parts[4] = [...days].sort((a, b) => a - b).map(n => names[n]).join(',');
    return parts.join(' ');
  } catch {
    return expr;
  }
}

export function cronToLabel(expr: string | null | undefined): string {
  if (!expr) return '';
  const parts = storedCronForEditing(expr).split(/\s+/);
  if (parts.length !== 5) return expr;
  const [min, hour, day, month, dow] = parts;
  if (parts.every(p => p === '*')) return '每分钟';
  if (day !== '*' || month !== '*' || !/^\d+$/.test(min) || !/^\d+$/.test(hour)) return expr;
  const time = `${hour}:${min.padStart(2, '0')}`;
  if (dow === '*') return `每天 ${time}`;
  const days = dow.split(',');
  if (days.some(d => !names.includes(d))) return expr;
  return `每周${days.map(d => labels[names.indexOf(d)]).join('、')} ${time}`;
}
