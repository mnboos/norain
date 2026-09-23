/**
 * Ride quality *presentation*: the YlOrRd colour ramp for a server-computed score, and where
 * along the drawn line each sample sits.
 *
 * The scoring itself - curves, weights, sensitivity - lives only in the backend
 * (`core/ride_quality.py`) and never ships to the browser: samples arrive with `rideScore`
 * and `rideLabel` already set. Do not add a curve, weight or threshold here.
 *
 * Kept free of maplibre/vue imports so the pure parts stay unit-testable, same as
 * `weatherIcons.ts`. Anything the server could not score is `null` and painted neutral
 * grey, never a colour from the ramp.
 */

import type { ForecastSampleOut } from "@norain/api/models";

/**
 * ColorBrewer YlOrRd-9 without its palest step: pale yellow = good ride, dark red = bad.
 *
 * Sequential and monotone in lightness, so the order survives colour-vision deficiency and
 * greyscale. `#ffffcc` is dropped because it vanishes into the light basemap. Even so, the
 * legend, the "Fahrqualität" line in each popup and the section list all repeat the
 * information in text - never rely on the colour by itself.
 */
export const YLORRD_8 = [
    "#ffeda0",
    "#fed976",
    "#feb24c",
    "#fd8d3c",
    "#fc4e2a",
    "#e31a1c",
    "#bd0026",
    "#800026",
] as const;

/** Cool blue-grey for "we don't know" - deliberately off the warm ramp. */
export const NO_DATA_COLOR = "#9aa5b1";

// The casing under every ramp-coloured line: the map route line and the list glyph. The good
// end of the ramp is very pale (#ffeda0, #fed976 sit near 1.2:1 against a light background),
// so a white casing would let it vanish - the casing carries the ink instead and flips with
// the theme. Same values NiceChart.vue uses for chart ink. A thin, half-transparent edge is
// enough to hold the pale end; a solid one reads as a heavy black outline next to yellow.
export const CASING_LIGHT = "#1b2733";
export const CASING_DARK = "#e8eef2";
export const CASING_OPACITY = 0.55;

// The journey alternatives the user has not picked, one colour per variant: all off the warm
// ramp and the blue line drawn when there is no forecast. Lighter on the dark basemap.
const ALTERNATIVE_LIGHT: readonly [string, ...string[]] = ["#0d9488", "#7c3aed", "#65a30d", "#64748b"];
const ALTERNATIVE_DARK: readonly [string, ...string[]] = ["#2dd4bf", "#a78bfa", "#a3e635", "#94a3b8"];

/** The colour of the variant at `index` in the day's list, stable whichever one is picked. */
export function alternativeColor(index: number, dark: boolean): string {
    const palette = dark ? ALTERNATIVE_DARK : ALTERNATIVE_LIGHT;
    return palette[index % palette.length] ?? palette[0];
}

/** One ramp step. Gradient spans are subdivided so no two stops jump further than this. */
const MAX_SCORE_STEP = 1 / (YLORRD_8.length - 1);

/** Smallest gap between two line-gradient stops - also what makes a "hard edge" hard. */
const EPS = 1e-4;

function clamp01(x: number): number {
    return x < 0 ? 0 : x > 1 ? 1 : x;
}

export function hexToRgb(hex: string): [number, number, number] {
    const n = Number.parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex(r: number, g: number, b: number): string {
    const part = (v: number) =>
        Math.round(Math.min(255, Math.max(0, v)))
            .toString(16)
            .padStart(2, "0");
    return `#${part(r)}${part(g)}${part(b)}`;
}

/** Ramp colour for a score, or the neutral grey for `null`/non-finite. */
export function scoreColor(score: number | null | undefined): string {
    if (score == null || !Number.isFinite(score)) return NO_DATA_COLOR;
    const t = clamp01(score) * (YLORRD_8.length - 1);
    const i = Math.min(Math.floor(t), YLORRD_8.length - 2);
    const lo = YLORRD_8[i];
    const hi = YLORRD_8[i + 1];
    if (!lo || !hi) return NO_DATA_COLOR;
    const f = t - i;
    const [r0, g0, b0] = hexToRgb(lo);
    const [r1, g1, b1] = hexToRgb(hi);
    return rgbToHex(r0 + (r1 - r0) * f, g0 + (g1 - g0) * f, b0 + (b1 - b0) * f);
}

/** How many "sehr gut … sehr schlecht" bands the server's `rideLabel` uses. */
const BAND_COUNT = 5;

/**
 * Which of the five quality bands a score falls in (0 = sehr gut .. 4 = sehr schlecht),
 * or `null` when the score is unknown. Also what the map thins chips on, so a band change
 * always keeps a chip beside it - the colour is never the only cue that conditions shifted.
 */
export function scoreBand(score: number | null | undefined): number | null {
    if (score == null || !Number.isFinite(score)) return null;
    return Math.min(BAND_COUNT - 1, Math.floor(clamp01(score) * BAND_COUNT));
}

const EARTH_R_M = 6371008.8;

export function haversineM(a: readonly number[], b: readonly number[]): number {
    const toRad = Math.PI / 180;
    const lon1 = (a[0] ?? 0) * toRad;
    const lat1 = (a[1] ?? 0) * toRad;
    const lon2 = (b[0] ?? 0) * toRad;
    const lat2 = (b[1] ?? 0) * toRad;
    const dLat = lat2 - lat1;
    const dLon = lon2 - lon1;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_R_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Force a strictly increasing 0..1 sequence; rescale rather than run past 1. */
function normalizeStops(ps: number[]): number[] {
    let last = -Infinity;
    const out = ps.map(p => {
        const v = !Number.isFinite(p) || p <= last ? last + EPS : p;
        last = v;
        return v;
    });
    const max = out[out.length - 1] ?? 0;
    return max > 1 ? out.map(p => p / max) : out;
}

/**
 * Where each sample sits along the line, as a 0..1 fraction of *distance* - which is what
 * maplibre's `line-progress` measures. The obvious `elapsedS / totalSeconds` is a *time*
 * fraction and puts the colours in the wrong place wherever speed varies (climbs, city vs.
 * open road); it survives here only as the fallback for degenerate geometry.
 *
 * Samples are polyline vertices (the backend picks vertex indices in `_sample_indices` and
 * copies the unrounded `coords[idx]`), so the match is by exact coordinate, scanning
 * forward from the previous hit. Nearest-vertex matching would be wrong on an out-and-back
 * route - the return leg passes within metres of the outbound one and would capture the
 * lookup.
 */
export function sampleProgress(
    line: readonly (readonly number[])[],
    samples: readonly ForecastSampleOut[],
    totalSeconds: number,
): number[] {
    const byTime = () => normalizeStops(samples.map(s => clamp01(s.elapsedS / (totalSeconds || 1))));
    if (line.length < 2 || samples.length === 0) return byTime();

    const cum: number[] = [0];
    for (let i = 1; i < line.length; i++) {
        cum.push((cum[i - 1] ?? 0) + haversineM(line[i - 1] ?? [], line[i] ?? []));
    }
    const total = cum[cum.length - 1] ?? 0;
    if (!(total > 0)) return byTime();

    const out: number[] = [];
    let ptr = 0;
    for (const s of samples) {
        let idx = -1;
        for (let i = ptr; i < line.length; i++) {
            const v = line[i];
            if (v?.[0] === s.lon && v[1] === s.lat) {
                idx = i;
                break;
            }
        }
        if (idx < 0) {
            // No exact hit (shouldn't happen, but a re-projected or trimmed line would do
            // it): nearest vertex in the *remaining* window, so ordering still holds.
            let bestD = Infinity;
            idx = ptr;
            for (let i = ptr; i < line.length; i++) {
                const v = line[i];
                if (!v) continue;
                const d = ((v[0] ?? 0) - s.lon) ** 2 + ((v[1] ?? 0) - s.lat) ** 2;
                if (d < bestD) {
                    bestD = d;
                    idx = i;
                }
            }
        }
        ptr = idx;
        out.push(clamp01((cum[idx] ?? 0) / total));
    }
    return normalizeStops(out);
}

/**
 * Flat `[progress, color, ...]` list for maplibre's `line-gradient`.
 *
 * Two things this has to get right that one-stop-per-sample does not:
 *
 * 1. maplibre blends between the two hexes it is given, as a straight sRGB chord. Samples
 *    scoring 0.15 and 0.75 would blend #fed976 straight to #bd0026, skipping every ramp
 *    step between them and cutting the corner through sRGB. Headwind flips sign whenever the
 *    route turns, so jumps that size are routine. Each span is therefore subdivided until
 *    no two stops are more than one ramp step apart.
 * 2. A span with an unknown endpoint is painted flat grey with hard edges. Letting grey
 *    gradate into a real colour would invent "partly known" weather over ground where the
 *    data is fine.
 */
export function gradientStops(progress: readonly number[], scores: readonly (number | null)[]): (number | string)[] {
    const n = Math.min(progress.length, scores.length);
    const flat = (color: string): (number | string)[] => [0, color, 1, color];
    if (n === 0) return flat(NO_DATA_COLOR);
    if (n === 1) return flat(scoreColor(scores[0] ?? null));

    const pts: { p: number; color: string }[] = [];
    for (let i = 0; i < n - 1; i++) {
        const p0 = progress[i] ?? 0;
        const p1 = progress[i + 1] ?? 1;
        const s0 = scores[i] ?? null;
        const s1 = scores[i + 1] ?? null;

        if (s0 == null || s1 == null) {
            pts.push({ p: p0, color: NO_DATA_COLOR }, { p: p1, color: NO_DATA_COLOR });
            continue;
        }
        const steps = Math.max(1, Math.ceil(Math.abs(s1 - s0) / MAX_SCORE_STEP));
        for (let k = 0; k <= steps; k++) {
            const t = k / steps;
            pts.push({ p: p0 + (p1 - p0) * t, color: scoreColor(s0 + (s1 - s0) * t) });
        }
    }

    // Anchor both ends so the whole line is painted, not just the sampled middle.
    const first = pts[0];
    const last = pts[pts.length - 1];
    if (first && first.p > 0) pts.unshift({ p: 0, color: first.color });
    if (last && last.p < 1) pts.push({ p: 1, color: last.color });

    // Coincident stops (a known span meeting an unknown one) get nudged apart by EPS,
    // which is what turns the boundary into a hard edge instead of a blend.
    const ps = normalizeStops(pts.map(pt => pt.p));
    return pts.flatMap((pt, i) => [ps[i] ?? 0, pt.color]);
}
