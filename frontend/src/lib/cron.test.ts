import { describe, expect, it } from 'vitest';
import { cronToLabel, storedCronForEditing } from './cron';

describe('persisted cron weekdays', () => {
  it('preserves legacy numeric meaning when opening an editor', () => {
    expect(storedCronForEditing('0 18 * * 0')).toBe('0 18 * * mon');
    expect(storedCronForEditing('30 14 * * 1-5')).toBe('30 14 * * tue,wed,thu,fri,sat');
    expect(storedCronForEditing('0 18 * * sun')).toBe('0 18 * * sun');
  });
  it('labels persisted numbers and names consistently', () => {
    expect(cronToLabel('0 18 * * 0')).toBe('每周一 18:00');
    expect(cronToLabel('0 18 * * sun')).toBe('每周日 18:00');
    expect(cronToLabel('30 14 * * mon-fri')).toBe('每周一、二、三、四、五 14:30');
    expect(cronToLabel('0 18 1 * *')).toBe('0 18 1 * *');
  });
});
