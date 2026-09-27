import { haversineM } from "./rideQuality";

/**
 * The selected place on the route is one continuous position: a share (0..1) of the route's
 * distance, not a sample index. The map, the elevation profile and the forecast charts each
 * convert it into their own axis, so hovering any of them moves the selection in all of them,
 * between forecast samples too. The details panel shows the sample nearest to it.
 */

export interface ScreenPoint { x: number; y: number }

/**
 * `ys` at `x`, linear between the neighbouring `xs` (ascending, repeats allowed), clamped to
 * the ends. Undefined without data.
 */
export function interpolate(x: number, xs: readonly number[], ys: readonly number[]): number | undefined {
    const n = Math.min(xs.length, ys.length);
    if (!n || !Number.isFinite(x)) return undefined;
    if (x <= (xs[0] ?? 0)) return ys[0];
    if (x >= (xs[n - 1] ?? 0)) return ys[n - 1];
    // First index whose x is >= the value.
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if ((xs[mid] ?? 0) < x) lo = mid + 1;
        else hi = mid;
    }
    const x0 = xs[lo - 1] ?? 0;
    const x1 = xs[lo] ?? 0;
    const y0 = ys[lo - 1] ?? 0;
    const y1 = ys[lo] ?? 0;
    return x1 > x0 ? y0 + ((y1 - y0) * (x - x0)) / (x1 - x0) : y1;
}

/** The index of the value nearest to `target`. */
export function nearestIndex(values: readonly number[], target: number): number | undefined {
    let selected: number | undefined;
    let distance = Infinity;
    values.forEach((value, index) => {
        const delta = Math.abs(value - target);
        if (delta < distance) {
            selected = index;
            distance = delta;
        }
    });
    return selected;
}

/**
 * A position measured on one line, measured on another line through the same samples. Two
 * levels of detail of one route differ a little in length, so a share of one is not quite the
 * same share of the other; pinned to the samples, which every line level keeps exactly, a
 * selected sample lands on its own vertex whichever line is drawn.
 */
export function remapProgress(position: number, from: readonly number[], to: readonly number[]): number {
    if (from.length !== to.length || !from.length) return position;
    return interpolate(position, [0, ...from, 1], [0, ...to, 1]) ?? position;
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

/** The `[lon, lat]` at a position along the line (`progress` from `lineProgress`). */
export function pointAtProgress(
    line: readonly (readonly number[])[],
    progress: readonly number[],
    position: number,
): [number, number] | undefined {
    if (!line.length || line.length !== progress.length || !Number.isFinite(position)) return undefined;
    let i = 1;
    while (i < line.length - 1 && (progress[i] ?? 0) < position) i++;
    const a = line[i - 1] ?? line[0] ?? [];
    const b = line[i] ?? a;
    const from = progress[i - 1] ?? 0;
    const to = progress[i] ?? from;
    const t = to > from ? Math.max(0, Math.min(1, (position - from) / (to - from))) : 0;
    return [(a[0] ?? 0) + t * ((b[0] ?? 0) - (a[0] ?? 0)), (a[1] ?? 0) + t * ((b[1] ?? 0) - (a[1] ?? 0))];
}

/**
 * The position under the pointer, projected onto the line itself: geographically close
 * stretches may be other legs of the ride. Undefined when the pointer is off the line.
 */
export function progressAtRoutePoint(
    pointer: ScreenPoint,
    line: readonly ScreenPoint[],
    progress: readonly number[],
    current = 0,
    tolerance = 12,
): number | undefined {
    let bestDistance = Infinity;
    let bestContinuity = Infinity;
    let selected: number | undefined;
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
        const continuity = Math.abs(position - current);
        // Only use continuity to disambiguate visually coincident segments (within half a pixel).
        if (distance < bestDistance - 0.5 || (Math.abs(distance - bestDistance) <= 0.5 && continuity < bestContinuity)) {
            bestDistance = distance;
            bestContinuity = continuity;
            selected = position;
        }
    }
    return selected;
}

interface Series { x?: unknown; y?: unknown }
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/**
 * The drawn point of a trace at `x`, linear between its two neighbouring points. Undefined
 * outside the trace and across a gap: the dot never bridges missing data.
 */
export function seriesPointAt(series: Series, x: number): { x: number; y: number } | undefined {
    if (!Array.isArray(series.x) || !Array.isArray(series.y) || !finite(x)) return undefined;
    for (let i = 0; i < series.x.length; i++) {
        const x0: unknown = series.x[i];
        const y0: unknown = series.y[i];
        if (!finite(x0) || !finite(y0)) continue;
        if (x0 === x) return { x, y: y0 };
        const x1: unknown = series.x[i + 1];
        const y1: unknown = series.y[i + 1];
        if (finite(x1) && finite(y1) && x0 < x && x < x1) {
            return { x, y: y0 + ((y1 - y0) * (x - x0)) / (x1 - x0) };
        }
    }
    return undefined;
}
