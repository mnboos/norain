import type { LineDetail } from "@/queries/forecastParts";

const ORDER: readonly LineDetail[] = ["coarse", "medium", "full"];

/**
 * How much route-line detail a zoom level can show.
 *
 * At Swiss latitudes a pixel is ~50 m at zoom 11 and ~6.5 m at zoom 14, so the coarse line
 * (~50 m tolerance) and the medium one (~10 m) each stay within about a pixel of the road
 * up to those zooms. Past zoom 14 only the full line does.
 */
export function lineDetailForZoom(zoom: number): LineDetail {
    if (zoom < 11) return "coarse";
    if (zoom < 14) return "medium";
    return "full";
}

/** The more detailed of two levels. */
export function finerDetail(a: LineDetail, b: LineDetail): LineDetail {
    return ORDER.indexOf(a) >= ORDER.indexOf(b) ? a : b;
}
