/**
 * Icon colours for the key ride data: raw readings (°C, km/h) mapped onto fixed colour scales.
 *
 * These are plain meteorological scales, not ride-quality curves - nothing here mirrors
 * `core/ride_quality.py`. Every stop sits at mid lightness so one hex reads on both the light
 * (#ffffff) and the dark (#1c2533) card. `null` means "no reading to colour"; the caller
 * falls back to its neutral colour.
 */

import { hexToRgb, rgbToHex } from "@/utils/rideQuality";

type Stops = readonly (readonly [number, string])[];

/** Cold blue -> teal -> green around the comfortable band -> orange -> red, in °C. */
export const TEMP_STOPS: Stops = [
    [-10, "#3b4cc0"],
    [0, "#3a70b8"],
    [8, "#2a9d8f"],
    [18, "#5a9e3a"],
    [25, "#e0a030"],
    [30, "#e0602a"],
    [35, "#b0203a"],
];

/** Calm grey -> orange -> red -> purple, anchored on the Beaufort limits (Bft 3/5/7/9) in km/h. */
export const WIND_STOPS: Stops = [
    [0, "#9aa5b1"],
    [12, "#e0a030"],
    [29, "#e0602a"],
    [50, "#b0203a"],
    [75, "#6a1b6a"],
];

/** Linear RGB interpolation between value-anchored stops, clamped at both ends. */
export function rampAt(stops: Stops, value: number): string {
    const first = stops[0];
    const last = stops[stops.length - 1];
    if (!first || !last) throw new Error("rampAt needs at least one stop");
    if (value <= first[0]) return first[1];
    if (value >= last[0]) return last[1];
    const i = stops.findIndex(([v]) => v > value);
    const lo = stops[i - 1];
    const hi = stops[i];
    if (!lo || !hi) return last[1];
    const [v0, c0] = lo;
    const [v1, c1] = hi;
    const f = (value - v0) / (v1 - v0);
    const [r0, g0, b0] = hexToRgb(c0);
    const [r1, g1, b1] = hexToRgb(c1);
    return rgbToHex(r0 + (r1 - r0) * f, g0 + (g1 - g0) * f, b0 + (b1 - b0) * f);
}

export function temperatureColor(celsius: number | null | undefined): string | null {
    if (celsius == null || !Number.isFinite(celsius)) return null;
    return rampAt(TEMP_STOPS, celsius);
}

/** No colour for no headwind: calm or a tailwind has nothing to warn about. */
export function headwindColor(kmh: number | null | undefined): string | null {
    if (kmh == null || !Number.isFinite(kmh) || kmh <= 0) return null;
    return rampAt(WIND_STOPS, kmh);
}
