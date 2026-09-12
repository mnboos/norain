/**
 * Ride quality: one 0..1 score per weather sample, and the Spectral colour ramp the map
 * paints the route line with.
 *
 * Kept free of maplibre/vue imports so the pure parts stay unit-testable, same as
 * `weatherIcons.ts`.
 *
 * The score is *derived* - it has no physical unit. Everything that shows it (the legend,
 * the popup) has to say so, and anything we can't compute stays `null` rather than
 * guessing: a stretch with no usable precipitation data is painted neutral grey, never a
 * colour from the ramp.
 */

import type { WeatherSample } from "@norain/api/models";

/**
 * ColorBrewer Spectral-10, reversed: violet = good ride, dark red = bad.
 *
 * Spectral is a diverging palette carrying a sequential quantity here, and it is a
 * red-green ramp, so hue alone is not readable for everyone. The legend, the
 * "Fahrqualität" line in each popup and the section list all repeat the information in
 * text - never rely on the colour by itself.
 */
export const SPECTRAL_10 = [
    "#5e4fa2",
    "#3288bd",
    "#66c2a5",
    "#abdda4",
    "#e6f598",
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d53e4f",
    "#9e0142",
] as const;

/** Neutral grey for "we don't know" - deliberately not a Spectral step. */
export const NO_DATA_COLOR = "#9aa5b1";

/** One Spectral step. Gradient spans are subdivided so no two stops jump further than this. */
const MAX_SCORE_STEP = 1 / (SPECTRAL_10.length - 1);

/** Smallest gap between two line-gradient stops - also what makes a "hard edge" hard. */
const EPS = 1e-4;

export type RideFactor = "rain" | "wind" | "temp";

/**
 * The subset of a sample the scorer actually reads.
 *
 * The route-list thumbnails ship only these five fields per point instead of a whole
 * `WeatherSample` (a list of routes would otherwise carry every forecast in full). Taking
 * the narrow type here lets both callers share one implementation - porting the curves
 * anywhere else would let the thumbnail and the map drift apart on the same route.
 */
export type RideInput = Pick<
    WeatherSample,
    "rainMm" | "precipitationIntervalS" | "rainRateMmH" | "temp" | "headwind"
>;

export interface RideScore {
    /** 0 = bestes Wetter, 1 = schlechtestes. Derived, unitless. */
    score: number;
    /** The three penalties, each 0..1, before weighting. */
    rain: number;
    wind: number;
    temp: number;
    /** Largest *weighted* contributor - what the popup names as the reason. */
    worst: RideFactor;
}

const FACTORS: readonly RideFactor[] = ["rain", "wind", "temp"];
const WEIGHTS: Record<RideFactor, number> = { rain: 0.55, wind: 0.25, temp: 0.2 };

function clamp01(x: number): number {
    return x < 0 ? 0 : x > 1 ? 1 : x;
}

/** Linear interpolation through a sorted (x, y) table, flat outside the ends. */
function piecewise(x: number, points: readonly (readonly [number, number])[]): number {
    const first = points[0];
    const last = points[points.length - 1];
    if (!first || !last) return 0;
    if (x <= first[0]) return first[1];
    for (let i = 1; i < points.length; i++) {
        const lo = points[i - 1];
        const hi = points[i];
        if (!lo || !hi) break;
        if (x <= hi[0]) {
            const span = hi[0] - lo[0];
            return span === 0 ? hi[1] : lo[1] + ((x - lo[0]) / span) * (hi[1] - lo[1]);
        }
    }
    return last[1];
}

// Rain dominates - the app is called NoRain. Drizzle is a nuisance, 5 mm/h is the worst
// it gets for scoring purposes.
const RAIN_CURVE = [
    [0, 0],
    [0.2, 0.15],
    [1, 0.5],
    [2.5, 0.8],
    [5, 1],
] as const;

// Headwind only. A tailwind is not "better than calm" on this scale, it just isn't a
// penalty, so the curve starts at 0.
const WIND_CURVE = [
    [0, 0],
    [10, 0.3],
    [20, 0.65],
    [30, 1],
] as const;

// Comfortable riding band is 14-22 °C; it gets worse in both directions.
const TEMP_CURVE = [
    [-2, 1],
    [14, 0],
    [22, 0],
    [34, 1],
] as const;

/**
 * Precipitation rate in mm/h, or `null` when it cannot be derived.
 *
 * `rainMm` is an accumulation over `precipitationIntervalS`, so without the interval it
 * is not a rate and we must not pretend it is one - an hourly assumption would silently
 * turn a 15-minute bucket into a quarter of the real intensity.
 */
export function rainRateMmH(sample: RideInput): number | null {
    if (sample.rainRateMmH != null && Number.isFinite(sample.rainRateMmH)) return sample.rainRateMmH;
    const interval = sample.precipitationIntervalS;
    if (interval != null && interval > 0 && Number.isFinite(sample.rainMm)) {
        return (sample.rainMm * 3600) / interval;
    }
    return null;
}

/**
 * Combined ride quality for one sample, or `null` when the precipitation rate is unknown.
 * `temp`, `headwind` and `windSpeed` are non-nullable on the wire, so only rain can
 * knock out a sample.
 */
export function rideScore(sample: RideInput): RideScore | null {
    const rate = rainRateMmH(sample);
    if (rate == null) return null;

    const rain = clamp01(piecewise(rate, RAIN_CURVE));
    const wind = clamp01(piecewise(sample.headwind, WIND_CURVE));
    const temp = clamp01(piecewise(sample.temp, TEMP_CURVE));

    const weighted: Record<RideFactor, number> = {
        rain: rain * WEIGHTS.rain,
        wind: wind * WEIGHTS.wind,
        temp: temp * WEIGHTS.temp,
    };
    let worst: RideFactor = "rain";
    for (const f of FACTORS) if (weighted[f] > weighted[worst]) worst = f;

    return { score: clamp01(weighted.rain + weighted.wind + weighted.temp), rain, wind, temp, worst };
}

function hexToRgb(hex: string): [number, number, number] {
    const n = Number.parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
    const part = (v: number) =>
        Math.round(Math.min(255, Math.max(0, v)))
            .toString(16)
            .padStart(2, "0");
    return `#${part(r)}${part(g)}${part(b)}`;
}

/** Spectral colour for a score, or the neutral grey for `null`/non-finite. */
export function scoreColor(score: number | null | undefined): string {
    if (score == null || !Number.isFinite(score)) return NO_DATA_COLOR;
    const t = clamp01(score) * (SPECTRAL_10.length - 1);
    const i = Math.min(Math.floor(t), SPECTRAL_10.length - 2);
    const lo = SPECTRAL_10[i];
    const hi = SPECTRAL_10[i + 1];
    if (!lo || !hi) return NO_DATA_COLOR;
    const f = t - i;
    const [r0, g0, b0] = hexToRgb(lo);
    const [r1, g1, b1] = hexToRgb(hi);
    return rgbToHex(r0 + (r1 - r0) * f, g0 + (g1 - g0) * f, b0 + (b1 - b0) * f);
}

const BAND_LABELS = ["sehr gut", "gut", "mässig", "schlecht", "sehr schlecht"] as const;

const FACTOR_LABELS: Record<RideFactor, string> = { rain: "Regen", wind: "Wind", temp: "Temperatur" };

/**
 * Naming a single cause is only honest when one factor actually dominates. Below this
 * share of the total the ride is simply "mixed", and the label stays silent about why.
 */
const MIN_WORST_SHARE = 0.5;

/**
 * Which of the five quality bands a score falls in (0 = sehr gut .. 4 = sehr schlecht),
 * or `null` when the score is unknown. Also what the map thins chips on, so a band change
 * always keeps a chip beside it - the colour is never the only cue that conditions shifted.
 */
export function scoreBand(score: number | null | undefined): number | null {
    if (score == null || !Number.isFinite(score)) return null;
    return Math.min(BAND_LABELS.length - 1, Math.floor(clamp01(score) * BAND_LABELS.length));
}

/** German wording for the popup, e.g. "mässig · v. a. Regen". */
export function rideScoreLabel(rq: RideScore | null): string {
    const band = rq ? scoreBand(rq.score) : null;
    if (!rq || band === null) return "Nicht verfügbar";
    const label = BAND_LABELS[band] ?? "";
    if (band === 0) return label;
    const share = rq.score > 0 ? (rq[rq.worst] * WEIGHTS[rq.worst]) / rq.score : 0;
    return share >= MIN_WORST_SHARE ? `${label} · v. a. ${FACTOR_LABELS[rq.worst]}` : label;
}

const EARTH_R_M = 6371008.8;

function haversineM(a: readonly number[], b: readonly number[]): number {
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
    samples: readonly WeatherSample[],
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
 *    scoring 0.15 and 0.75 would blend #3288bd straight to #d53e4f, skipping every
 *    Spectral step between them and passing through mud. Headwind flips sign whenever the
 *    route turns, so jumps that size are routine. Each span is therefore subdivided until
 *    no two stops are more than one Spectral step apart.
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
