import { describe, expect, it } from "vitest";
import { RecurringRouteInToJSON, RecurringRouteOutFromJSON } from "@norain/api/models";

describe("paired ride API contract", () => {
    it("sends separate return schedule fields in camelCase", () => {
        const payload = RecurringRouteInToJSON({
            name: "Work",
            startLat: 47,
            startLon: 9,
            startName: "Home",
            destLat: 47.1,
            destLon: 9.1,
            destName: "Work",
            scheduleCron: "0 8 * * 1-5",
            scheduleDescription: "Morning",
            returnScheduleCron: "0 17 * * 1-5",
            returnScheduleDescription: "Evening",
        });
        expect(payload.returnScheduleCron).toBe("0 17 * * 1-5");
        expect(payload.scheduleCron).toBe("0 8 * * 1-5");
    });
    it("reads navigation IDs for each direction", () => {
        const outward = RecurringRouteOutFromJSON({
            id: "out",
            return_route_id: "back",
            return_schedule_description: "Evening",
        });
        const returning = RecurringRouteOutFromJSON({ id: "back", parent_route_id: "out" });
        expect(outward.returnRouteId).toBe("back");
        expect(returning.parentRouteId).toBe("out");
    });
});
