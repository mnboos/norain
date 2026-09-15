import { describe, expect, it } from "vitest";
import { ForecastJobOutFromJSON, type WindArrow } from "@norain/api/models";
import {
    compassLabel, groundArrowBearing, groundWindText, relativeWindLabel, visibleWindArrows, windArrowSize,
    windPowerText,
} from "../wind";

// Wind from the east while riding north.
const segment: WindArrow = { lon: 9, lat: 47, bearing: 0, windDir: 90, windSpeed: 12, windPowerW: 40 };

describe("wind presentation", () => {
    it("points the arrow where the real wind blows, whatever the direction of travel", () => {
        expect(groundArrowBearing(segment)).toBe(270);
        expect(groundArrowBearing({ ...segment, bearing: 180 })).toBe(270);
        expect(groundArrowBearing({ ...segment, windDir: 350 })).toBe(170);
        expect(relativeWindLabel(-45)).toBe("von vorne links");
        expect(relativeWindLabel(180)).toBe("von hinten");
        expect(relativeWindLabel(-180)).toBe("von hinten");
        expect(compassLabel(225)).toBe("SW");
        expect(groundWindText(segment)).toBe("12 km/h aus O, von rechts");
        expect(groundWindText({ ...segment, bearing: 90 })).toBe("12 km/h aus O, von vorne");
    });
    it("does not invent a direction for calm or unknown data", () => {
        expect(groundArrowBearing({ ...segment, windDir: null })).toBeNull();
        expect(groundWindText({ ...segment, windDir: null, windSpeed: 0 })).toContain("keine Richtung");
        expect(groundWindText({ ...segment, windSpeed: null })).toContain("nicht verfügbar");
    });
    it("words and sizes the wind effort the server chose", () => {
        expect(windPowerText("mittel")).toBe("Windaufwand mittel (geschätzt)");
        expect(windPowerText("Wind hilft")).toBe("Wind hilft (geschätzt)");
        expect(windPowerText("keiner")).toBe("Kein Windaufwand (geschätzt)");
        expect(windPowerText(null)).toContain("nicht verfügbar");
        expect(windArrowSize(null)).toBe(20);
        expect(windArrowSize(0)).toBe(20);
        expect(windArrowSize(0.5)).toBe(26);
        expect(windArrowSize(1)).toBe(32);
    });
    it("thins in screen space, caps arrows, and skips arrows it cannot place", () => {
        const all = Array.from({ length: 220 }, (_, i) => ({ ...segment, lon: i }));
        const visible = visibleWindArrows(all, s => ({ x: s.lon * 40, y: 0 }));
        expect(visible).toHaveLength(100);
        expect(visible[1]?.lon).toBe(2);
        expect(visibleWindArrows([segment], () => ({ x: NaN, y: 0 }))).toEqual([]);
    });
    it("reads old raw WebSocket jobs without new wind properties", () => {
        const job = ForecastJobOutFromJSON({ job_id: "old", status: "done", result: {
            line: [], total_seconds: 0, total_distance_m: 0, samples: [], departure_time: "2026-09-12T12:00",
            summary: { will_rain: false, max_rain_mm: 0, rain_amount: 0, source: "open-meteo", max_headwind: 0 },
        } });
        expect(job.result?.windArrows ?? []).toEqual([]);
        expect(job.result?.summary.windDistribution ?? null).toBeNull();
    });
});
