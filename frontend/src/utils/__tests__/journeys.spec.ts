import { describe, expect, it } from "vitest";
import { journeyIsBusy } from "@/queries/journeys";
import { clock, duration, journeyDates, km } from "@/utils/journeys";
import { poiCategory, poiName } from "@/utils/poiCategories";

describe("journey formatting", () => {
    it("formats distance and riding time", () => {
        expect(km(8460)).toBe("8.5 km");
        expect(km(84_500)).toBe("85 km");
        expect(duration(3 * 3600 + 5 * 60)).toBe("3 h 05");
        expect(duration(40 * 60)).toBe("40 min");
    });

    it("reads the clock time as the server sent it, never converting it", () => {
        expect(clock("2030-06-01T08:45:00+02:00")).toBe("08:45");
        expect(clock(null)).toBe("");
    });

    it("spans the journey's days", () => {
        const dates = journeyDates({ startDate: new Date("2030-06-01T00:00:00Z"), dayCount: 3 });
        expect(dates).toContain("1.");
        expect(dates).toContain("3.");
    });
});

describe("POI names", () => {
    it("falls back to the category when OSM has no name", () => {
        expect(poiName({ name: "", category: "drinking_water" })).toBe("Trinkwasser");
        expect(poiName({ name: "Dorfbrunnen", category: "drinking_water" })).toBe("Dorfbrunnen");
        expect(poiCategory("unknown").label).toBe("unknown");
    });
});

describe("journeyIsBusy", () => {
    it("is busy while planning or while a stage forecast runs", () => {
        expect(journeyIsBusy({ planStatus: "routing" })).toBe(true);
        expect(journeyIsBusy({ planStatus: "done", days: [] })).toBe(false);
        expect(journeyIsBusy({ planStatus: "done", days: [{ stages: [{ forecastStatus: "fetching" }] }] })).toBe(true);
        const done = { planStatus: "done", days: [{ stages: [{ forecastStatus: "done" }, { forecastStatus: null }] }] };
        expect(journeyIsBusy(done)).toBe(false);
    });
});
