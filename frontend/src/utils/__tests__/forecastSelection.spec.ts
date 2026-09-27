import { describe, expect, it } from "vitest";
import {
    interpolate,
    nearestIndex,
    pointAtProgress,
    progressAtRoutePoint,
    remapProgress,
    seriesPointAt,
} from "../forecastSelection";

describe("route position helpers", () => {
    it("interpolates between neighbours and clamps at the ends", () => {
        const xs = [0, 10, 20];
        const ys = [100, 200, 400];
        expect(interpolate(5, xs, ys)).toBe(150);
        expect(interpolate(15, xs, ys)).toBe(300);
        expect(interpolate(10, xs, ys)).toBe(200);
        expect(interpolate(-3, xs, ys)).toBe(100);
        expect(interpolate(99, xs, ys)).toBe(400);
        expect(interpolate(5, [], [])).toBeUndefined();
    });

    it("finds the nearest value", () => {
        expect(nearestIndex([0, 0.3, 0.7, 1], 0.45)).toBe(1);
        expect(nearestIndex([0, 0.3, 0.7, 1], 0.9)).toBe(3);
        expect(nearestIndex([], 0.5)).toBeUndefined();
    });

    it("pins positions of one line level to another at the samples", () => {
        const coarse = [0.2, 0.6];
        const fine = [0.25, 0.55];
        // A sample lands exactly on its own place on the other line.
        expect(remapProgress(0.2, coarse, fine)).toBeCloseTo(0.25);
        expect(remapProgress(0.6, coarse, fine)).toBeCloseTo(0.55);
        // Between samples, linear; the ends stay the ends.
        expect(remapProgress(0.4, coarse, fine)).toBeCloseTo(0.4);
        expect(remapProgress(0, coarse, fine)).toBe(0);
        expect(remapProgress(1, coarse, fine)).toBe(1);
        // Without samples (a plain preview line) nothing moves.
        expect(remapProgress(0.3, [], [])).toBe(0.3);
    });

    it("places a position between two vertices", () => {
        const line = [
            [8, 47],
            [9, 47],
            [9, 48],
        ];
        const progress = [0, 0.5, 1];
        expect(pointAtProgress(line, progress, 0.25)).toEqual([8.5, 47]);
        expect(pointAtProgress(line, progress, 0.75)).toEqual([9, 47.5]);
        expect(pointAtProgress(line, progress, 0)).toEqual([8, 47]);
        expect(pointAtProgress(line, progress, 1)).toEqual([9, 48]);
    });

    it("reads a continuous position off the line under the pointer", () => {
        const line = [
            { x: 0, y: 0 },
            { x: 100, y: 0 },
        ];
        expect(progressAtRoutePoint({ x: 37, y: 4 }, line, [0, 1])).toBeCloseTo(0.37);
        expect(progressAtRoutePoint({ x: 37, y: 40 }, line, [0, 1])).toBeUndefined();
    });

    it("prefers the leg nearest the current position where two legs overlap", () => {
        // Out and back along the same street.
        const line = [
            { x: 0, y: 0 },
            { x: 100, y: 0 },
            { x: 0, y: 0 },
        ];
        const progress = [0, 0.5, 1];
        expect(progressAtRoutePoint({ x: 40, y: 0 }, line, progress, 0.1)).toBeCloseTo(0.2);
        expect(progressAtRoutePoint({ x: 40, y: 0 }, line, progress, 0.9)).toBeCloseTo(0.8);
    });

    it("finds a trace's point anywhere along it, never across a gap", () => {
        const series = { x: [0, 10, 20, 30], y: [1, 3, null, 5] };
        expect(seriesPointAt(series, 5)).toEqual({ x: 5, y: 2 });
        expect(seriesPointAt(series, 10)).toEqual({ x: 10, y: 3 });
        expect(seriesPointAt(series, 15)).toBeUndefined();
        expect(seriesPointAt(series, 30)).toEqual({ x: 30, y: 5 });
        expect(seriesPointAt(series, 40)).toBeUndefined();
    });
});
