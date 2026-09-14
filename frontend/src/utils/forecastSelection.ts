import { haversineM } from "./rideQuality";

export interface TimedSample { elapsedS: number }
export interface ScreenPoint { x: number; y: number }

export function nearestSampleByTime(samples: readonly TimedSample[], minutes: number): number | undefined {
    if (!Number.isFinite(minutes)) return undefined;
    let selected: number | undefined;
    let distance = Infinity;
    samples.forEach((sample, index) => {
        const delta = Math.abs(sample.elapsedS / 60 - minutes);
        if (delta < distance) {
            selected = index;
            distance = delta;
        }
    });
    return selected;
}

/** Physical distance fractions, independent of map projection and riding speed. */
export function lineProgress(line: readonly (readonly number[])[]): number[] {
    let total = 0;
    const distances = line.map((point, i) => {
        const previous = line[i - 1];
        if (previous) total += haversineM(previous, point);
        return total;
    });
    return distances.map(distance => total > 0 ? distance / total : 0);
}

/** Project onto the actual line first; geographically close samples may be on another leg. */
export function sampleAtRoutePoint(
    pointer: ScreenPoint,
    line: readonly ScreenPoint[],
    progress: readonly number[],
    samples: readonly number[],
    current = 0,
    tolerance = 12,
): number | undefined {
    let bestDistance = Infinity;
    let bestContinuity = Infinity;
    let selectedProgress: number | undefined;
    for (let i = 1; i < line.length; i++) {
        const a = line[i - 1];
        const b = line[i];
        const from = progress[i - 1];
        const to = progress[i];
        if (!a || !b || from === undefined || to === undefined) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const length2 = dx * dx + dy * dy;
        if (!length2) continue;
        const t = Math.max(0, Math.min(1, ((pointer.x - a.x) * dx + (pointer.y - a.y) * dy) / length2));
        const distance = Math.hypot(pointer.x - a.x - t * dx, pointer.y - a.y - t * dy);
        if (distance > tolerance) continue;
        const position = from + t * (to - from);
        const continuity = Math.abs(position - (samples[current] ?? 0));
        // Only use continuity to disambiguate visually coincident segments (within half a pixel).
        if (distance < bestDistance - 0.5 || (Math.abs(distance - bestDistance) <= 0.5 && continuity < bestContinuity)) {
            bestDistance = distance;
            bestContinuity = continuity;
            selectedProgress = position;
        }
    }
    if (selectedProgress === undefined) return undefined;
    let selected: number | undefined;
    let distance = Infinity;
    samples.forEach((position, index) => {
        const delta = Math.abs(position - selectedProgress);
        if (delta < distance) {
            selected = index;
            distance = delta;
        }
    });
    return selected;
}

interface Series { x?: unknown; y?: unknown; customdata?: unknown; legendgroup?: string }
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** Sample-backed traces use IDs, never trace point offsets (bands can contain only a subset). */
export function selectedSeriesPoint(series: Series, index: number, minutes: number): { x: number; y: number } | undefined {
    if (!Array.isArray(series.x) || !Array.isArray(series.y)) return undefined;
    const custom = series.customdata;
    if (Array.isArray(custom)) {
        const point = custom.findIndex(value => Array.isArray(value) && value[0] === index);
        if (point >= 0) {
            const x: unknown = series.x[point];
            const y: unknown = series.y[point];
            return finite(x) && finite(y) ? { x, y } : undefined;
        }
    }
    // Felt wind is sampled more densely, with no forecast sample IDs. Its segments are linear.
    if (series.legendgroup !== "felt" || !finite(minutes)) return undefined;
    for (let i = 0; i < series.x.length; i++) {
        const x: unknown = series.x[i];
        const y: unknown = series.y[i];
        if (!finite(x) || !finite(y)) continue;
        if (x === minutes) return { x, y };
        const nextX: unknown = series.x[i + 1];
        const nextY: unknown = series.y[i + 1];
        if (finite(nextX) && finite(nextY) && x < minutes && minutes < nextX) {
            return { x: minutes, y: y + (nextY - y) * (minutes - x) / (nextX - x) };
        }
    }
    return undefined;
}
