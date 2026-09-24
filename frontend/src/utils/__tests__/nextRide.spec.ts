import { describe, expect, it } from "vitest";
import { nextRideId } from "../nextRide";

const route = {
    id: "outbound",
    returnRouteId: "homeward",
    nextDeparture: "2026-09-25T06:30:00+02:00",
    returnNextDeparture: "2026-09-24T17:00:00+02:00",
};

describe("next scheduled ride direction", () => {
    it("opens the homeward route when it departs before the next outbound ride", () => {
        expect(nextRideId(route)).toBe("homeward");
    });
    it("keeps the outbound route when it departs first", () => {
        expect(nextRideId({ ...route, nextDeparture: "2026-09-24T06:30:00+02:00" })).toBe("outbound");
    });
    it("handles missing schedules and unpaired routes", () => {
        expect(nextRideId({ ...route, nextDeparture: null })).toBe("homeward");
        expect(nextRideId({ ...route, returnNextDeparture: null })).toBe("outbound");
        expect(nextRideId({ ...route, returnRouteId: null })).toBe("outbound");
    });
    it("compares instants across timezone offsets", () => {
        expect(nextRideId({ ...route, nextDeparture: "2026-09-24T16:30:00Z" })).toBe("homeward");
    });
});
