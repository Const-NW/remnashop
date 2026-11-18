export const DAY_MS = 86_400_000;

/** A list filter's period in days; 0 is all time. */
export type PeriodDays = 0 | 7 | 30 | 90;
export const PERIOD_DAYS: readonly PeriodDays[] = [0, 7, 30, 90];

// "all time" has no previous window; trends then cover the last month
export const ALL_TIME_TREND_DAYS = 30;

/** Filters for a summary as it looked one period ago (ALL_TIME_TREND_DAYS back for "all time"). */
export function previousWindow(period: number, now = Date.now()): { createdFrom?: string; createdTo: string } {
  const shift = (period || ALL_TIME_TREND_DAYS) * DAY_MS;
  return {
    createdFrom: period ? new Date(now - 2 * shift).toISOString() : undefined,
    createdTo: new Date(now - shift).toISOString(),
  };
}

/** The end of a plan term of `days` from `start` (ms), as the backend counts it: a multiple
 * of 365 is calendar years, of 30 calendar months (the month's last day when shorter), else days. */
export function addTerm(start: number, days: number): number {
  const months = days % 365 === 0 ? (days / 365) * 12 : days % 30 === 0 ? days / 30 : 0;
  if (months === 0) return start + days * DAY_MS;
  const date = new Date(start);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + months);
  const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(day, lastDay));
  return date.getTime();
}
