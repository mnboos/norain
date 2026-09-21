import { describe, expect, it } from "vitest";
import { headwindColor, rampAt, TEMP_STOPS, temperatureColor, WIND_STOPS } from "@/utils/statColors";

const STOPS = [
    [0, "#000000"],
    [10, "#ffffff"],
] as const;

describe("rampAt", () => {
    it("returns the stop colour exactly at an anchor", () => {
        expect(rampAt(STOPS, 0)).toBe("#000000");
        expect(rampAt(STOPS, 10)).toBe("#ffffff");
    });

    it("clamps below the first and above the last stop", () => {
        expect(rampAt(STOPS, -5)).toBe("#000000");
        expect(rampAt(STOPS, 50)).toBe("#ffffff");
    });

    it("interpolates between stops", () => {
        expect(rampAt(STOPS, 5)).toBe("#808080");
    });
});

describe("temperatureColor", () => {
    it("runs from the cold end to the hot end of the scale", () => {
        expect(temperatureColor(-20)).toBe(TEMP_STOPS[0]?.[1]);
        expect(temperatureColor(40)).toBe(TEMP_STOPS[TEMP_STOPS.length - 1]?.[1]);
        expect(temperatureColor(0)).not.toBe(temperatureColor(20));
    });

    it("has no colour without a reading", () => {
        expect(temperatureColor(null)).toBeNull();
        expect(temperatureColor(Number.NaN)).toBeNull();
    });
});

describe("headwindColor", () => {
    it("has no colour for calm, tailwind or no reading", () => {
        expect(headwindColor(0)).toBeNull();
        expect(headwindColor(-8)).toBeNull();
        expect(headwindColor(null)).toBeNull();
    });

    it("reaches the strongest stop for a gale", () => {
        expect(headwindColor(100)).toBe(WIND_STOPS[WIND_STOPS.length - 1]?.[1]);
        expect(headwindColor(5)).not.toBe(headwindColor(40));
    });
});
