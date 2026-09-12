import { describe, expect, it } from "vitest";
import { ForecastJobOutFromJSON, type WindArrow } from "@norain/api/models";
import { apparentArrowBearing, feltWindText, relativeWindLabel, visibleWindArrows } from "../wind";
import { rideScore } from "../rideQuality";

const segment: WindArrow = { lon: 9, lat: 47, bearing: 0, feltAngle: 45, feltSpeed: 20 };

describe("felt wind presentation", () => {
    it("uses relative direction with the correct geographic arrow", () => {
        expect(apparentArrowBearing(segment)).toBe(225);
        expect(apparentArrowBearing({ ...segment, bearing: 350, feltAngle: 30 })).toBe(200);
        expect(relativeWindLabel(-45)).toBe("von vorne links");
        expect(relativeWindLabel(180)).toBe("von hinten");
        expect(relativeWindLabel(-180)).toBe("von hinten");
        expect(feltWindText(segment)).toContain("20 km/h von vorne rechts");
    });
    it("does not invent direction for zero apparent wind or unknown data", () => {
        expect(apparentArrowBearing({ ...segment, feltAngle: null })).toBeNull();
        expect(feltWindText({ ...segment, feltAngle: null, feltSpeed: 0 })).toContain("kein gerichteter Luftzug");
        expect(feltWindText({ ...segment, feltSpeed: null })).toContain("nicht verfügbar");
    });
    it("thins in screen space, caps arrows, and skips arrows it cannot place", () => {
        const all = Array.from({ length: 220 }, (_, i) => ({ ...segment, lon: i }));
        const visible = visibleWindArrows(all, s => ({ x: s.lon * 40, y: 0 }));
        expect(visible).toHaveLength(100);
        expect(visible[1]?.lon).toBe(2);
        expect(visibleWindArrows([segment], () => ({ x: NaN, y: 0 }))).toEqual([]);
    });
    it("keeps unknown wind out of the ride score, but calm remains valid", () => {
        const input = { rainMm: 0, rainRateMmH: 0, temp: 18, headwind: null };
        expect(rideScore(input)).toBeNull();
        expect(rideScore({ ...input, headwind: 0 })?.score).toBe(0);
        expect(rideScore({ ...input, headwind: NaN })).toBeNull();
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
