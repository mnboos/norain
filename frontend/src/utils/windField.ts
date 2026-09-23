import type { WindArrow } from "@norain/api/models";
import { groundArrowBearing, type GroundWind } from "@/utils/wind";

/**
 * A wind field for the particle animation, built from the route's own wind arrows.
 *
 * The forecast only knows the wind along the route (each point at its ride-time ETA), so the
 * field covers a corridor around the line and nothing else: particles never claim a wind the
 * forecast does not have. Cells are square in Web Mercator; row 0 is the northern edge, so
 * a cell's y grows southward like Mercator y.
 *
 * The field is built once, out to the widest corridor. How much of it is shown depends on the
 * zoom (`corridorHalfWidthM`), so zooming never rebuilds it: each cell keeps its distance to
 * the route and `corridorMask` cuts the corridor at draw time.
 */
export interface WindField {
    /** Mercator (0..1 world) position of the north-west corner. */
    x0: number;
    y0: number;
    /** Edge length of one cell, in Mercator units. */
    cell: number;
    width: number;
    height: number;
    /** Metres per Mercator unit at the route's middle latitude. */
    metresPerUnit: number;
    /** The widest corridor the field covers, metres either side of the route. */
    maxHalfWidthM: number;
    /** Wind blowing east / north, km/h, per cell (0 outside the widest corridor). */
    u: Float32Array;
    v: Float32Array;
    /** Distance to the route, metres; `maxHalfWidthM` for every cell beyond it. */
    distance: Float32Array;
    /** Cells within the widest corridor, the only ones particles may be born in. */
    inside: Uint32Array;
}

export interface WindFieldOptions {
    /** The widest corridor, metres either side of the route. */
    halfWidthM?: number;
    /** Target cell size, metres. */
    cellM?: number;
    /** Longest grid side, cells. */
    maxSide?: number;
    /** Arrows averaged per cell. */
    neighbours?: number;
}

const EARTH_CIRCUMFERENCE_M = 40_075_016.686;
/** Tile size MapLibre uses for the world: world px = 512 · 2^zoom. */
const WORLD_TILE_PX = 512;

/** Corridor half-width on screen, px either side of the route, at any zoom, */
export const CORRIDOR_HALF_WIDTH_PX = 160;
/** but never narrower than this (street zoom) or wider than `MAX_CORRIDOR_M` (whole route). */
export const MIN_CORRIDOR_M = 500;
export const MAX_CORRIDOR_M = 8000;
/**
 * Share of the half-width over which the corridor fades out: full strength near the route,
 * easing to nothing at the edge, so the corridor has no visible border.
 */
export const CORRIDOR_FADE_SHARE = 0.7;

/** Corridor half-width in metres at this zoom: about the same width on screen at every zoom. */
export function corridorHalfWidthM(field: Pick<WindField, "metresPerUnit" | "maxHalfWidthM">, zoom: number): number {
    const metresPerPx = field.metresPerUnit / (WORLD_TILE_PX * 2 ** zoom);
    return Math.min(field.maxHalfWidthM, Math.max(MIN_CORRIDOR_M, CORRIDOR_HALF_WIDTH_PX * metresPerPx));
}

/** 1 near the route, easing (smoothstep) to 0 at the corridor's edge. */
export function corridorMask(distanceM: number, halfWidthM: number): number {
    const fadeStart = halfWidthM * (1 - CORRIDOR_FADE_SHARE);
    const t = Math.max(0, Math.min(1, (distanceM - fadeStart) / (halfWidthM - fadeStart)));
    return 1 - t * t * (3 - 2 * t);
}

export function mercatorX(lon: number): number {
    return (lon + 180) / 360;
}

export function mercatorY(lat: number): number {
    const phi = (lat * Math.PI) / 180;
    return (1 - Math.log(Math.tan(Math.PI / 4 + phi / 2)) / Math.PI) / 2;
}

/** Ground wind as east/north components in km/h; `undefined` without speed or direction. */
export function windComponents(wind: GroundWind): { u: number; v: number } | undefined {
    const toward = groundArrowBearing(wind);
    const speed = wind.windSpeed;
    if (toward == null || speed == null || !Number.isFinite(speed)) return undefined;
    const rad = (toward * Math.PI) / 180;
    return { u: speed * Math.sin(rad), v: speed * Math.cos(rad) };
}

function segmentDistanceSq(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSq = dx * dx + dy * dy;
    const t = lengthSq > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq)) : 0;
    const cx = ax + t * dx - px;
    const cy = ay + t * dy - py;
    return cx * cx + cy * cy;
}

export function buildWindField(
    arrows: readonly WindArrow[],
    line: readonly number[][],
    options: WindFieldOptions = {},
): WindField | undefined {
    const { halfWidthM = MAX_CORRIDOR_M, cellM = 300, maxSide = 1024, neighbours = 4 } = options;
    const winds = arrows.flatMap(arrow => {
        const wind = windComponents(arrow);
        return wind ? [{ x: mercatorX(arrow.lon), y: mercatorY(arrow.lat), ...wind }] : [];
    });
    const points = line.flatMap(([lon, lat]) =>
        lon == null || lat == null ? [] : [{ x: mercatorX(lon), y: mercatorY(lat) }],
    );
    if (!winds.length || !points.length) return undefined;

    // Metres per Mercator unit at the route's middle latitude; a route spans too little
    // latitude for the difference across it to matter at this resolution.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of points) {
        minX = Math.min(minX, p.x);
        minY = Math.min(minY, p.y);
        maxX = Math.max(maxX, p.x);
        maxY = Math.max(maxY, p.y);
    }
    const midLat = (Math.atan(Math.sinh(Math.PI * (1 - (minY + maxY)))) * 180) / Math.PI;
    const metresPerUnit = EARTH_CIRCUMFERENCE_M * Math.cos((midLat * Math.PI) / 180);
    const halfWidth = halfWidthM / metresPerUnit;
    minX -= halfWidth;
    minY -= halfWidth;
    maxX += halfWidth;
    maxY += halfWidth;
    const cell = Math.max(cellM / metresPerUnit, Math.max(maxX - minX, maxY - minY) / maxSide);
    const width = Math.max(1, Math.ceil((maxX - minX) / cell));
    const height = Math.max(1, Math.ceil((maxY - minY) / cell));

    // The line at the grid's resolution: vertices closer than a cell add nothing to the
    // distance but cost a full neighbourhood of cells each.
    const route = [points[0] ?? { x: 0, y: 0 }];
    for (const p of points) {
        const last = route[route.length - 1];
        if (last && Math.hypot(p.x - last.x, p.y - last.y) >= cell) route.push(p);
    }
    const end = points[points.length - 1];
    if (end && route[route.length - 1] !== end) route.push(end);

    // Distance to the line, and which segment is nearest: each segment only visits the cells
    // within reach of it.
    const distanceSq = new Float32Array(width * height).fill(Infinity);
    const nearestSegment = new Int32Array(width * height).fill(-1);
    const reach = Math.ceil(halfWidth / cell);
    route.forEach((a, i) => {
        const b = route[i + 1] ?? a;
        const cx0 = Math.max(0, Math.floor((Math.min(a.x, b.x) - minX) / cell) - reach);
        const cx1 = Math.min(width - 1, Math.floor((Math.max(a.x, b.x) - minX) / cell) + reach);
        const cy0 = Math.max(0, Math.floor((Math.min(a.y, b.y) - minY) / cell) - reach);
        const cy1 = Math.min(height - 1, Math.floor((Math.max(a.y, b.y) - minY) / cell) + reach);
        for (let cy = cy0; cy <= cy1; cy++) {
            const py = minY + (cy + 0.5) * cell;
            for (let cx = cx0; cx <= cx1; cx++) {
                const index = cy * width + cx;
                const d = segmentDistanceSq(minX + (cx + 0.5) * cell, py, a.x, a.y, b.x, b.y);
                if (d < (distanceSq[index] ?? Infinity)) {
                    distanceSq[index] = d;
                    nearestSegment[index] = i;
                }
            }
        }
    });

    // The arrows come in route order, so the arrows nearest a cell are the ones around the
    // point of the route nearest to it: each vertex gets its nearest arrow once, and a cell
    // then only weighs a short run of arrows around its segment's. That keeps the cost per
    // cell constant however wide the corridor and however dense the arrows.
    const vertexArrow = route.map(p => {
        let best = 0;
        let bestD = Infinity;
        winds.forEach((w, j) => {
            const d = (w.x - p.x) ** 2 + (w.y - p.y) ** 2;
            if (d < bestD) {
                bestD = d;
                best = j;
            }
        });
        return best;
    });
    const run = neighbours + 2;
    const candidates: { d: number; u: number; v: number }[] = [];

    const u = new Float32Array(width * height);
    const v = new Float32Array(width * height);
    const distance = new Float32Array(width * height).fill(halfWidthM);
    const inside: number[] = [];
    for (let index = 0; index < width * height; index++) {
        const d = Math.sqrt(distanceSq[index] ?? Infinity);
        if (!(d < halfWidth)) continue;
        const x = minX + ((index % width) + 0.5) * cell;
        const y = minY + (Math.floor(index / width) + 0.5) * cell;
        const segment = nearestSegment[index] ?? 0;
        const first = Math.min(vertexArrow[segment] ?? 0, vertexArrow[segment + 1] ?? winds.length);
        const last = Math.max(vertexArrow[segment] ?? 0, vertexArrow[segment + 1] ?? 0);
        candidates.length = 0;
        for (let j = Math.max(0, first - run); j <= Math.min(winds.length - 1, last + run); j++) {
            const w = winds[j];
            if (w) candidates.push({ d: Math.hypot(w.x - x, w.y - y), u: w.u, v: w.v });
        }
        candidates.sort((p, q) => p.d - q.d);
        let su = 0, sv = 0, sw = 0;
        for (const n of candidates.slice(0, neighbours)) {
            // Inverse-distance weights; the floor keeps a cell right on an arrow finite.
            const weight = 1 / Math.max(n.d * n.d, (cell * cell) / 16);
            su += n.u * weight;
            sv += n.v * weight;
            sw += weight;
        }
        if (!(sw > 0)) continue;
        u[index] = su / sw;
        v[index] = sv / sw;
        distance[index] = d * metresPerUnit;
        inside.push(index);
    }
    if (!inside.length) return undefined;
    return {
        x0: minX,
        y0: minY,
        cell,
        width,
        height,
        metresPerUnit,
        maxHalfWidthM: halfWidthM,
        u,
        v,
        distance,
        inside: Uint32Array.from(inside),
    };
}

/**
 * Bilinear wind and distance to the route at a position in cell units. Outside the grid the
 * wind is zero and the distance is the widest corridor's, so it is always outside.
 */
export function sampleWindField(field: WindField, fx: number, fy: number): { u: number; v: number; distance: number } {
    const x = fx - 0.5;
    const y = fy - 0.5;
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const tx = x - x0;
    const ty = y - y0;
    let u = 0, v = 0, distance = 0, covered = 0;
    for (let j = 0; j < 2; j++) {
        const cy = y0 + j;
        if (cy < 0 || cy >= field.height) continue;
        for (let i = 0; i < 2; i++) {
            const cx = x0 + i;
            if (cx < 0 || cx >= field.width) continue;
            const w = (i ? tx : 1 - tx) * (j ? ty : 1 - ty);
            const index = cy * field.width + cx;
            u += (field.u[index] ?? 0) * w;
            v += (field.v[index] ?? 0) * w;
            distance += (field.distance[index] ?? field.maxHalfWidthM) * w;
            covered += w;
        }
    }
    return { u, v, distance: distance + (1 - covered) * field.maxHalfWidthM };
}
