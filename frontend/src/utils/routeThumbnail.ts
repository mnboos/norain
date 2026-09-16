/**
 * The route-list thumbnail: which stored thumbnail still describes the next ride, and the
 * geometry that turns its geographic path into a square viewBox.
 *
 * Kept free of vue/quasar imports so the pure parts stay unit-testable, same as
 * `rideQuality.ts` and `weatherIcons.ts`. Colour and scoring live in `rideQuality.ts`;
 * this module only decides *where* the ink goes.
 */

import type { RecurringRouteOut, RouteThumbnail } from "@norain/api/models";

/** Only the fields the list's weather readers need, so callers and tests need not build a
 * whole route. */
export type ThumbnailRoute = Pick<RecurringRouteOut, "thumbnail" | "nextDeparture" | "hasGeometry">;

/**
 * The stored thumbnail when it still describes the ride that is coming, `null` otherwise.
 *
 * A thumbnail is computed for one departure and frozen; `nextDeparture` is recomputed on
 * every request. Once that ride has passed, the row rolls on to the next departure and the
 * stored samples describe the wrong one - so the glyph, the caption and the rain/frost icons
 * all fall back to "we don't know" rather than present yesterday's weather as today's. One
 * rule, three readers.
 */
export function liveThumbnail(route: ThumbnailRoute): RouteThumbnail | null {
    const thumbnail = route.thumbnail ?? null;
    if (!thumbnail) return null;
    // A route with no next departure at all also counts: whatever the thumbnail was computed
    // for is not a ride that is still coming.
    return thumbnail.departure && thumbnail.departure !== route.nextDeparture ? null : thumbnail;
}

/** Padding inside the viewBox, in the same units, so a 2px stroke is not clipped. */
const PAD = 2;

export interface Point {
    x: number;
    y: number;
}

/**
 * Project `[[lon, lat], ...]` into a square of `size`, preserving the route's shape.
 *
 * Two things this has to get right:
 *
 * 1. **Longitude is compressed by latitude.** One degree of longitude is `cos(lat)` as
 *    wide as one of latitude, so projecting raw degrees stretches every route
 *    east-west - at 47°N by about 1.5×. An equirectangular projection about the path's
 *    mean latitude fixes it; at the scale of one commute nothing better is warranted.
 * 2. **Fit, don't fill.** The scale is the *smaller* of the two axis scales and the
 *    result is centred, so a long thin route reads as long and thin. Stretching each
 *    axis to fill the square would redraw a straight commute as a diagonal sprawl -
 *    the glyph's whole job is to be recognisable at a glance.
 *
 * Returns `[]` for an empty path. A degenerate path (all points equal, or a single
 * point) lands centred rather than dividing by zero.
 */
export function projectPath(path: readonly (readonly number[])[], size: number): Point[] {
    if (path.length === 0) return [];

    const raw = flatten(path);
    const { minX, minY, width: w, height: h } = boundsOf(raw);

    const span = size - 2 * PAD;
    // One scale for both axes - see "fit, don't fill" above.
    const scale = w === 0 && h === 0 ? 0 : span / Math.max(w, h);

    // Centre whichever axis is the shorter one.
    const offsetX = PAD + (span - w * scale) / 2;
    const offsetY = PAD + (span - h * scale) / 2;

    return raw.map(p => ({
        x: offsetX + (p.x - minX) * scale,
        y: offsetY + (p.y - minY) * scale,
    }));
}

/**
 * How wide and how tall the route really is, in the same unit for both axes (degrees of
 * latitude). The detail page lays its charts out along the longer side. `0, 0` for an empty path.
 */
export function pathExtent(path: readonly (readonly number[])[]): { width: number; height: number } {
    if (path.length === 0) return { width: 0, height: 0 };
    const { width, height } = boundsOf(flatten(path));
    return { width, height };
}

/** `[[lon, lat], ...]` -> points with longitude compressed by the mean latitude (see 1. above). */
function flatten(path: readonly (readonly number[])[]): Point[] {
    const meanLat = path.reduce((sum, p) => sum + (p[1] ?? 0), 0) / path.length;
    const kx = Math.cos((meanLat * Math.PI) / 180);
    // Flip y: SVG grows downward, latitude grows upward.
    return path.map(p => ({ x: (p[0] ?? 0) * kx, y: -(p[1] ?? 0) }));
}

function boundsOf(points: readonly Point[]): { minX: number; minY: number; width: number; height: number } {
    const xs = points.map(p => p.x);
    const ys = points.map(p => p.y);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    return { minX, minY, width: Math.max(...xs) - minX, height: Math.max(...ys) - minY };
}

/** `"x,y x,y ..."` for an SVG `<polyline points>`, rounded to keep the markup small. */
export function pointsAttr(points: readonly Point[]): string {
    return points.map(p => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
}
