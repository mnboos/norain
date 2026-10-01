import { describe, expect, it } from "vitest";

import { tp } from "@/i18n";

describe("tp", () => {
    it("reads the hiking variant for a hike", () => {
        expect(tp("hike", "routeForm.direction")).toBe("Richtung");
    });

    it("reads the plain key for every other profile, or none", () => {
        expect(tp("bike", "routeForm.direction")).toBe("Fahrtrichtung");
        expect(tp(undefined, "routeForm.direction")).toBe("Fahrtrichtung");
    });

    it("falls back to the plain key when a text has no hiking variant", () => {
        expect(tp("hike", "routeForm.name")).toBe("Name");
    });

    it("passes the parameters through", () => {
        expect(tp("hike", "journeyDay.departure", { time: "08:00" })).toBe("Start: 08:00");
    });
});
