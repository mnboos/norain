import { describe, expect, it } from "vitest";

import { pointsAttr, projectPath } from "../routeThumbnail";

const SIZE = 40;
const PAD = 2;

describe("projectPath", () => {
    it("returns nothing for an empty path", () => {
        expect(projectPath([], SIZE)).toEqual([]);
    });

    it("keeps every point inside the padded box", () => {
        const path = [
            [9.0, 47.0],
            [9.05, 47.02],
            [9.02, 47.05],
        ];
        for (const p of projectPath(path, SIZE)) {
            expect(p.x).toBeGreaterThanOrEqual(PAD - 1e-9);
            expect(p.x).toBeLessThanOrEqual(SIZE - PAD + 1e-9);
            expect(p.y).toBeGreaterThanOrEqual(PAD - 1e-9);
            expect(p.y).toBeLessThanOrEqual(SIZE - PAD + 1e-9);
        }
    });

    it("preserves aspect ratio instead of stretching to fill", () => {
        // Four times as wide as it is tall, in projected units.
        const path = [
            [9.0, 47.0],
            [9.4, 47.0],
            [9.4, 47.068],
            [9.0, 47.068],
        ];
        const pts = projectPath(path, SIZE);
        const width = Math.max(...pts.map(p => p.x)) - Math.min(...pts.map(p => p.x));
        const height = Math.max(...pts.map(p => p.y)) - Math.min(...pts.map(p => p.y));

        // Stretching to fill would make both equal the full span.
        expect(width).toBeGreaterThan(height * 2);
        expect(width).toBeCloseTo(SIZE - 2 * PAD, 5);
    });

    it("centres the shorter axis", () => {
        // A horizontal line: no vertical extent at all, so it belongs on the mid-line.
        const pts = projectPath(
            [
                [9.0, 47.0],
                [9.1, 47.0],
            ],
            SIZE,
        );
        for (const p of pts) expect(p.y).toBeCloseTo(SIZE / 2, 5);
    });

    it("compresses longitude by latitude rather than treating degrees as square", () => {
        // Equal degree spans: at 47°N the lon span covers ~cos(47) of the lat span, so
        // the drawn shape must be taller than it is wide.
        const path = [
            [9.0, 47.0],
            [9.1, 47.0],
            [9.1, 47.1],
        ];
        const pts = projectPath(path, SIZE);
        const width = Math.max(...pts.map(p => p.x)) - Math.min(...pts.map(p => p.x));
        const height = Math.max(...pts.map(p => p.y)) - Math.min(...pts.map(p => p.y));
        expect(height).toBeGreaterThan(width);
        expect(width / height).toBeCloseTo(Math.cos((47.05 * Math.PI) / 180), 2);
    });

    it("places a degenerate path at the centre without dividing by zero", () => {
        for (const path of [[[9.0, 47.0]], [[9.0, 47.0], [9.0, 47.0]]]) {
            for (const p of projectPath(path, SIZE)) {
                expect(Number.isFinite(p.x)).toBe(true);
                expect(Number.isFinite(p.y)).toBe(true);
                expect(p.x).toBeCloseTo(SIZE / 2, 5);
                expect(p.y).toBeCloseTo(SIZE / 2, 5);
            }
        }
    });
});

describe("pointsAttr", () => {
    it("formats points for an SVG polyline", () => {
        expect(pointsAttr([{ x: 1.234, y: 5.678 }, { x: 9, y: 10 }])).toBe("1.23,5.68 9.00,10.00");
    });
});
