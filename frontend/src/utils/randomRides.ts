import { duration, km } from "@/utils/journeys";

/**
 * The pace each profile's routing model gives on mixed roads (backend
 * core/random_rides.py NOMINAL_SPEED_KMH). Only for the form's rough "≈" hint: the server
 * sizes every ride by the riding time GraphHopper actually returns.
 */
export const NOMINAL_SPEED_KMH: Record<string, number> = { bike: 18, ebike: 22, fast_ebike: 32 };

export const HEADING_OPTIONS: { label: string; value: number | null }[] = [
    { label: "Egal", value: null },
    { label: "Norden", value: 0 },
    { label: "Nordosten", value: 45 },
    { label: "Osten", value: 90 },
    { label: "Südosten", value: 135 },
    { label: "Süden", value: 180 },
    { label: "Südwesten", value: 225 },
    { label: "Westen", value: 270 },
    { label: "Nordwesten", value: 315 },
];

export function headingLabel(heading: number | null | undefined): string {
    return HEADING_OPTIONS.find(o => o.value === (heading ?? null))?.label ?? `${heading}°`;
}

/** What the other measure of a length target is, roughly, at the profile's pace. */
export function paceHint(profile: string, target: { hours?: number; km?: number }): string {
    const speed = NOMINAL_SPEED_KMH[profile] ?? 18;
    if (target.hours != null) return `≈ ${km(target.hours * speed * 1000)} bei ca. ${speed} km/h`;
    if (target.km != null) return `≈ ${duration((target.km / speed) * 3600)} bei ca. ${speed} km/h`;
    return "";
}
