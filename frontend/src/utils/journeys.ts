import { JourneyReasonOutKindEnum as Kind, type JourneyOut, type JourneyReasonOut } from "@norain/api/models";

import { intlLocale, t, te } from "@/i18n";
import { poiCategory } from "@/utils/poiCategories";

export function planStatusLabel(status: string): string {
    const key = `journeys.status.${status}`;
    return te(key) ? t(key) : status;
}

/** "Sa, 27. Sept." in Swiss local time; `Date` from the client is midnight UTC of that day. */
export function dayLabel(date: Date): string {
    return date.toLocaleDateString(intlLocale(), { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
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
    return parts.length ? t("journeys.gapWithout", { excess: parts.join(" / ") }) : "";
}

/** Reasons that say the plan misses what was asked for; the rest explain the ranking. */
const PLANNING_WARNINGS = new Set<string>([Kind.DayLimit, Kind.LegLimit, Kind.MissingStop]);

export function isPlanningWarning(reason: JourneyReasonOut): boolean {
    return PLANNING_WARNINGS.has(reason.kind);
}

/** "~12 min zu lang" or "~2.5 km zu weit": how far a limit is overrun. */
function overrun(reason: JourneyReasonOut): string {
    return reason.minutes != null
        ? t("journeys.reason.tooLong", { minutes: reason.minutes })
        : t("journeys.reason.tooFar", { km: reason.km ?? 0 });
}

/**
 * Why a variant ranks where it does, in words. The server sends the reason as data
 * (`core/journeys.py` `rank_day`); a kind this build does not know says nothing.
 */
export function reasonText(reason: JourneyReasonOut): string {
    const category = reason.category ? poiCategory(reason.category).label : "";
    switch (reason.kind) {
        case Kind.MissingStop:
            return t("journeys.reason.missingStop", { category });
        case Kind.Gap: {
            const parts = [
                ...(reason.minutes != null ? [`${reason.minutes} min`] : []),
                ...(reason.km != null ? [`${reason.km} km`] : []),
            ];
            return `${category}: ${t("journeys.gapWithout", { excess: parts.join(" / ") })}`;
        }
        case Kind.Detour:
            return t("journeys.reason.detour", { km: reason.km ?? 0, name: reason.name ?? category });
        case Kind.Longer:
            return t("journeys.reason.longer", { percent: reason.percent ?? 0 });
        case Kind.DayLimit:
            return t("journeys.reason.dayLimit", { overrun: overrun(reason) });
        case Kind.LegLimit:
            return t("journeys.reason.legLimit", { leg: reason.leg ?? 0, overrun: overrun(reason) });
        default:
            return "";
    }
}
