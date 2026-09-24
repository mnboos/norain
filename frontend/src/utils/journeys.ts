import type { JourneyOut } from "@norain/api/models";

const PLAN_STATUS: Record<string, string> = {
    pending: "Wird geplant",
    routing: "Wird geplant",
    weather: "Wetter entlang der Strecke",
    done: "Geplant",
    failed: "Fehlgeschlagen",
};

export function planStatusLabel(status: string): string {
    return PLAN_STATUS[status] ?? status;
}

/** "Sa, 27. Sept." in Swiss local time; `Date` from the client is midnight UTC of that day. */
export function dayLabel(date: Date): string {
    return date.toLocaleDateString("de-CH", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}

export function journeyDates(journey: Pick<JourneyOut, "startDate" | "dayCount">): string {
    const first = dayLabel(journey.startDate);
    if (!journey.dayCount || journey.dayCount < 2) return first;
    const last = new Date(journey.startDate.getTime() + (journey.dayCount - 1) * 86_400_000);
    return `${first} – ${dayLabel(last)}`;
}

export function km(meters: number): string {
    return `${(meters / 1000).toFixed(meters < 10_000 ? 1 : 0)} km`;
}

export function duration(seconds: number): string {
    const minutes = Math.round(seconds / 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}

/** The wall-clock time of an ISO timestamp with offset, as the server sent it: "08:30". */
export function clock(iso: string | null | undefined): string {
    return iso ? iso.slice(11, 16) : "";
}

/** Legacy gaps contain metres only; never infer historical travel times. */
export function gapExcessLabel(gap: number | { s?: number | null; m: number }, seconds?: number | null, meters?: number | null): string {
    const parts: string[] = [];
    if (typeof gap !== "number" && gap.s != null && seconds && gap.s > seconds) parts.push(duration(gap.s));
    const distance = typeof gap === "number" ? gap : gap.m;
    if (meters && distance > meters) parts.push(km(distance));
    return parts.length ? `${parts.join(" / ")} ohne` : "";
}
