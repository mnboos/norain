import { t } from "@/i18n";
import { duration, km } from "@/utils/journeys";

/**
 * The pace each profile's routing model gives on mixed roads (backend
 * core/random_rides.py NOMINAL_SPEED_KMH). Only for the form's rough "≈" hint: the server
 * sizes every ride by the riding time GraphHopper actually returns.
 */
export const NOMINAL_SPEED_KMH: Record<string, number> = { bike: 18, ebike: 22, fast_ebike: 32, hike: 4 };

const HEADINGS = [null, 0, 45, 90, 135, 180, 225, 270, 315] as const;

/** The direction choices of the random-ride form, labelled in the current language. */
export function headingOptions(): { label: string; value: number | null }[] {
    return HEADINGS.map(value => ({ label: t(`random.heading.${value ?? "any"}`), value }));
}

export function headingLabel(heading: number | null | undefined): string {
    return headingOptions().find(o => o.value === (heading ?? null))?.label ?? `${heading}°`;
}

/** What the other measure of a length target is, roughly, at the profile's pace. */
export function paceHint(profile: string, target: { hours?: number; km?: number }): string {
    const speed = NOMINAL_SPEED_KMH[profile] ?? 18;
    if (target.hours != null) return t("random.paceHint", { value: km(target.hours * speed * 1000), speed });
    if (target.km != null) return t("random.paceHint", { value: duration((target.km / speed) * 3600), speed });
    return "";
}
