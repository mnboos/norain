/**
 * Geometry for the tiny route glyph in the list: geographic path -> square viewBox.
 *
 * Kept free of vue/quasar imports so the pure parts stay unit-testable, same as
 * `rideQuality.ts` and `weatherIcons.ts`. Colour and scoring live in `rideQuality.ts`;
 * this module only decides *where* the ink goes.
 */

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

    const meanLat = path.reduce((sum, p) => sum + (p[1] ?? 0), 0) / path.length;
    const kx = Math.cos((meanLat * Math.PI) / 180);

    // Flip y: SVG grows downward, latitude grows upward.
    const raw = path.map(p => ({ x: (p[0] ?? 0) * kx, y: -(p[1] ?? 0) }));

    const xs = raw.map(p => p.x);
    const ys = raw.map(p => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    const span = size - 2 * PAD;
    const w = maxX - minX;
    const h = maxY - minY;
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

/** `"x,y x,y ..."` for an SVG `<polyline points>`, rounded to keep the markup small. */
export function pointsAttr(points: readonly Point[]): string {
    return points.map(p => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
}
