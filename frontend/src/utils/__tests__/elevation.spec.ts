import { describe, expect, it } from "vitest";
import { elevationFigure, smoothElevation } from "../elevation";

describe("elevation chart", () => {
    const points = [
        { distanceM: 0, elapsedS: 0, elevationM: -10 },
        { distanceM: 1500, elapsedS: 600, elevationM: null },
        { distanceM: 3000, elapsedS: 900, elevationM: 500 },
    ];
    const main = { points, color: "#000", label: "Höhe", primary: true };
    it("shows distance or actual riding time and preserves gaps and negative heights", () => {
        expect(elevationFigure([main], "distance").data[0]).toMatchObject({
            x: [0, 1.5, 3],
            y: [-10, null, 500],
            connectgaps: false,
        });
        expect(elevationFigure([main], "time").data[0]).toMatchObject({ x: [0, 10, 15] });
    });
    it("draws every alternative in its own colour, the primary last and thickest", () => {
        const other = { points: points.slice(0, 1), color: "#0d9488", label: "Variante 2" };
        const data = elevationFigure([main, other], "distance").data;
        expect(data).toHaveLength(2);
        expect(data[0]).toMatchObject({ name: "Variante 2", line: { color: "#0d9488", width: 2 } });
        expect(data[1]).toMatchObject({ name: "Höhe", line: { color: "#000", width: 3.5 } });
    });
    it("smooths only within the radius and never across a gap", () => {
        const bumpy = [0, 10, 20, 30, 40].map((d, i) => ({ distanceM: d, elapsedS: d, elevationM: i === 2 ? 30 : 0 }));
        const smoothed = smoothElevation(bumpy, 15);
        expect(smoothed[2]).toBeCloseTo((30 + (1 / 3) * 0 * 2) / (1 + 2 / 3));
        expect(smoothed[0]).toBe(0);
        expect(smoothElevation(points, 5000)).toEqual([-10, null, 500]);
        expect(smoothElevation(bumpy, 0)).toEqual([0, 0, 30, 0, 0]);
    });
});
