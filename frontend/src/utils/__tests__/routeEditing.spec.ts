import { describe, expect, it } from "vitest";

import { nearestVertex, routeCaption, viaInsertIndex } from "../routeEditing";

// West to east along one latitude, 0.01° apart.
const LINE = [0, 1, 2, 3, 4, 5, 6].map(i => [9 + i * 0.01, 47]);
const at = (line: number[][], i: number) => line[i] ?? [];

describe("nearestVertex", () => {
    it("finds the closest vertex", () => {
        expect(nearestVertex(LINE, [9.031, 47.001])).toBe(3);
    });

    it("searches only from the given index on", () => {
        expect(nearestVertex(LINE, [9.0, 47], 2)).toBe(2);
    });
});

describe("viaInsertIndex", () => {
    it("puts the first via point first", () => {
        expect(viaInsertIndex(LINE, [], [9.03, 47])).toBe(0);
    });

    it("inserts between the via points the grab lies between", () => {
        const vias = [at(LINE, 1), at(LINE, 4)];
        expect(viaInsertIndex(LINE, vias, [9.005, 47])).toBe(0);
        expect(viaInsertIndex(LINE, vias, [9.025, 47])).toBe(1);
        expect(viaInsertIndex(LINE, vias, [9.055, 47])).toBe(2);
    });

    it("keeps riding order on a line that passes one spot twice", () => {
        // Out to the east and back again: vertices 0..3 then 4..6 retrace them.
        const outAndBack = [9.0, 9.01, 9.02, 9.03, 9.02, 9.01, 9.0].map(lon => [lon, 47]);
        // One via point on the way out, one on the way back, both near 9.01.
        const vias = [at(outAndBack, 1), at(outAndBack, 5)];
        // A grab on the way back past the turn (nearest vertex 3) goes between them.
        expect(viaInsertIndex(outAndBack, vias, [9.03, 47])).toBe(1);
    });
});

describe("routeCaption", () => {
    it("shows kilometres and minutes", () => {
        expect(routeCaption(12_345, 42 * 60)).toBe("12.3 km · 42 min");
        expect(routeCaption(40_000, 95 * 60)).toBe("40.0 km · 1 h 35 min");
    });
});
