import { t } from "@/i18n";

/** Weekdays as cron numbers, Monday = 1 … Sunday = 7 (the route form's convention). */
export function weekdayLabels(): string[] {
    return [1, 2, 3, 4, 5, 6, 7].map(day => t(`schedule.weekday.${day}`));
}

/** A complete "HH:MM", or null: a half-typed time must never reach the cron string. */
export function parseTime(value: string): { h: number; m: number } | null {
    const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
    return match ? { h: Number(match[1]), m: Number(match[2]) } : null;
}

/** The cron for these weekdays at this time; "" while either is missing. */
export function weeklyCron(days: number[], time: string): string {
    const parsed = parseTime(time);
    if (!days.length || !parsed) return "";
    return `${parsed.m} ${parsed.h} * * ${[...days].sort().join(",")}`;
}

/** "Sa, So um 09:00"; "" while either is missing. */
export function weeklyDescription(days: number[], time: string): string {
    const parsed = parseTime(time);
    if (!days.length || !parsed) return "";
    const labels = weekdayLabels();
    const names = [...days].sort().map(d => labels[d - 1] ?? "");
    const clock = `${String(parsed.h).padStart(2, "0")}:${String(parsed.m).padStart(2, "0")}`;
    return t("schedule.daysAt", { days: names.join(", "), time: clock });
}

/** The weekday of a date as the ride's server sends it (midnight UTC of that day). */
export function cronWeekday(date: Date): number {
    return ((date.getUTCDay() + 6) % 7) + 1;
}
