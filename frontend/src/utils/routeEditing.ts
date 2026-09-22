import { haversineM } from "@/utils/rideQuality";

/** A [lon, lat] pair, the order GraphHopper and GeoJSON use. */
export type LonLat = [number, number];

/** A copy of an API coordinate pair as a LonLat. */
export function toLonLat(point: readonly number[]): LonLat {
    return [point[0] ?? 0, point[1] ?? 0];
}

/** Index of the line vertex nearest to `point`, searching from `from` onwards. */
export function nearestVertex(line: readonly (readonly number[])[], point: readonly number[], from = 0): number {
    let best = from;
    let bestDistance = Infinity;
    for (let i = from; i < line.length; i++) {
        const distance = haversineM(line[i] ?? [], point);
        if (distance < bestDistance) {
            best = i;
            bestDistance = distance;
        }
    }
    return best;
}

/**
 * Where a via point grabbed from the line at `grab` belongs in `vias`.
 *
 * Each via point is matched to the line in riding order, each search starting just after
 * the previous match, so a route that passes the same spot twice still gets its via points
 * in sequence. The new point goes after every via point the line reaches before `grab`.
 */
export function viaInsertIndex(
    line: readonly (readonly number[])[],
    vias: readonly (readonly number[])[],
    grab: readonly number[],
): number {
    const grabbed = nearestVertex(line, grab);
    let from = 0;
    let index = 0;
    for (const via of vias) {
        const at = nearestVertex(line, via, from);
        if (at >= grabbed) break;
        // The next via point comes strictly later along the line.
        from = at + 1;
        index++;
    }
    return index;
}

/** "12.3 km · 42 min" for the editor's caption. */
export function routeCaption(distanceM: number, timeS: number): string {
    const km = (distanceM / 1000).toFixed(1);
    const minutes = Math.round(timeS / 60);
    const duration = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
    return `${km} km · ${duration}`;
}
