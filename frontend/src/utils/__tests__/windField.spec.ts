import { describe, expect, it } from "vitest";
import type { WindArrow } from "@norain/api/models";
import {
    buildWindField, CORRIDOR_FADE_SHARE, corridorHalfWidthM, corridorMask, MAX_CORRIDOR_M, mercatorX, mercatorY, MIN_CORRIDOR_M,
    sampleWindField, windComponents,
} from "../windField";

// About 10 km due east along 47° N.
const line = [[9.0, 47.0], [9.07, 47.0], [9.14, 47.0]];
const arrowsFrom = (windDir: number, windSpeed = 20): WindArrow[] =>
    line.map(([lon, lat]) => ({ lon: lon ?? 0, lat: lat ?? 0, bearing: 90, windDir, windSpeed }));

function cellAt(field: NonNullable<ReturnType<typeof buildWindField>>, lon: number, lat: number) {
    return sampleWindField(field, (mercatorX(lon) - field.x0) / field.cell, (mercatorY(lat) - field.y0) / field.cell);
}

describe("wind field", () => {
    it("turns the direction the wind comes from into where it blows", () => {
        const west = windComponents({ windDir: 270, windSpeed: 10 });
        expect(west?.u).toBeCloseTo(10);
        expect(west?.v).toBeCloseTo(0);
        // From the north it blows south; the layer negates v, so it moves toward larger Mercator y.
        const north = windComponents({ windDir: 0, windSpeed: 10 });
        expect(north?.u).toBeCloseTo(0);
        expect(north?.v).toBeCloseTo(-10);
        expect(windComponents({ windDir: null, windSpeed: 10 })).toBeUndefined();
    });

    it("spreads a uniform wind evenly over the corridor", () => {
        const field = buildWindField(arrowsFrom(270), line);
        expect(field).toBeDefined();
        if (!field) return;
        for (const index of field.inside) {
            expect(field.u[index]).toBeCloseTo(20, 3);
            expect(field.v[index]).toBeCloseTo(0, 3);
        }
        const onRoute = cellAt(field, 9.05, 47.0);
        expect(onRoute.u).toBeCloseTo(20, 3);
        expect(onRoute.distance).toBeLessThan(300);
    });

    it("interpolates between different winds instead of jumping", () => {
        const arrows: WindArrow[] = [
            { lon: 9.0, lat: 47.0, bearing: 90, windDir: 270, windSpeed: 10 },
            { lon: 9.14, lat: 47.0, bearing: 90, windDir: 270, windSpeed: 30 },
        ];
        const field = buildWindField(arrows, line);
        if (!field) throw new Error("no field");
        const middle = cellAt(field, 9.07, 47.0).u;
        expect(middle).toBeGreaterThan(15);
        expect(middle).toBeLessThan(25);
        expect(cellAt(field, 9.0, 47.0).u).toBeLessThan(middle);
    });

    it("stops at the widest corridor and only spawns inside it", () => {
        const field = buildWindField(arrowsFrom(90), line, { halfWidthM: 2000 });
        if (!field) throw new Error("no field");
        expect(corridorMask(cellAt(field, 9.07, 47.0).distance, 2000)).toBeCloseTo(1);
        // ~3 km north of the line is beyond a 2 km half-width.
        const north = cellAt(field, 9.07, 47.027);
        expect(north.distance).toBeGreaterThanOrEqual(2000);
        expect(corridorMask(north.distance, 2000)).toBe(0);
        for (const index of field.inside) expect(field.distance[index]).toBeLessThan(2000);
        expect(field.inside.length).toBeLessThan(field.width * field.height);
    });

    it("keeps the corridor about as wide on screen at every zoom", () => {
        const field = buildWindField(arrowsFrom(90), line);
        if (!field) throw new Error("no field");
        const wide = corridorHalfWidthM(field, 11);
        const narrow = corridorHalfWidthM(field, 13);
        // Two zoom levels in, a pixel covers a quarter of the ground.
        expect(narrow).toBeCloseTo(wide / 4, 0);
        expect(corridorHalfWidthM(field, 20)).toBe(MIN_CORRIDOR_M);
        expect(corridorHalfWidthM(field, 5)).toBe(MAX_CORRIDOR_M);
        expect(field.maxHalfWidthM).toBe(MAX_CORRIDOR_M);
    });

    it("fades the corridor out smoothly instead of cutting it off", () => {
        expect(corridorMask(0, 1000)).toBe(1);
        expect(corridorMask(1000 * (1 - CORRIDOR_FADE_SHARE), 1000)).toBe(1);
        // Halfway through the fade, half strength; then down to nothing at the edge and beyond.
        expect(corridorMask(1000 * (1 - CORRIDOR_FADE_SHARE / 2), 1000)).toBeCloseTo(0.5);
        expect(corridorMask(1000, 1000)).toBe(0);
        expect(corridorMask(5000, 1000)).toBe(0);
        // Monotonic all the way out.
        let previous = 1;
        for (let d = 0; d <= 1000; d += 25) {
            const mask = corridorMask(d, 1000);
            expect(mask).toBeLessThanOrEqual(previous);
            previous = mask;
        }
    });

    it("gives each pass of an out-and-back its own ride-time wind, never a blend of both", () => {
        // Out east along 47.00° N, back west ~1.1 km further north. The wind turned between
        // the two passes, so the arrows (in route order, as the server sends them) disagree.
        const lons = Array.from({ length: 15 }, (_, i) => 9 + i * 0.01);
        const hairpin = [...lons.map(lon => [lon, 47.0]), ...[...lons].reverse().map(lon => [lon, 47.01])];
        const arrows: WindArrow[] = hairpin.map(([lon, lat], i) => ({
            lon: lon ?? 0, lat: lat ?? 0, bearing: i < lons.length ? 90 : 270,
            ...(i < lons.length ? { windDir: 270, windSpeed: 10 } : { windDir: 90, windSpeed: 30 }),
        }));
        const field = buildWindField(arrows, hairpin, { halfWidthM: 400 });
        if (!field) throw new Error("no field");
        expect(cellAt(field, 9.07, 47.0).u).toBeCloseTo(10, 3);
        expect(cellAt(field, 9.07, 47.01).u).toBeCloseTo(-30, 3);
    });

    it("stays finite with a single arrow or a single point", () => {
        const one = buildWindField(arrowsFrom(180).slice(0, 1), line);
        if (!one) throw new Error("no field");
        for (const index of one.inside) {
            expect(Number.isFinite(one.u[index])).toBe(true);
            expect(Number.isFinite(one.v[index])).toBe(true);
        }
        expect(buildWindField(arrowsFrom(180), [[9, 47]])).toBeDefined();
    });

    it("draws nothing without wind or without a line", () => {
        expect(buildWindField([], line)).toBeUndefined();
        expect(buildWindField(arrowsFrom(90), [])).toBeUndefined();
    });

    it("caps the grid on a long route", () => {
        const long = [[5.0, 46.0], [10.5, 47.8]];
        const field = buildWindField(
            long.map(([lon, lat]) => ({ lon: lon ?? 0, lat: lat ?? 0, bearing: 0, windDir: 0, windSpeed: 5 })),
            long,
            { maxSide: 256 },
        );
        expect(field && Math.max(field.width, field.height)).toBeLessThanOrEqual(256);
    });
});
