import { describe, expect, it } from "vitest";
import { cronWeekday, weeklyCron, weeklyDescription } from "../weeklySchedule";

describe("weeklySchedule", () => {
    it("builds the route form's cron and description", () => {
        expect(weeklyCron([6, 1], "09:05")).toBe("5 9 * * 1,6");
        expect(weeklyDescription([6, 1], "09:05")).toBe("Mo, Sa um 09:05");
    });

    it("gives nothing for a missing day or a half-typed time", () => {
        expect(weeklyCron([], "09:00")).toBe("");
        expect(weeklyCron([1], "09:__")).toBe("");
        expect(weeklyDescription([1], "25:00")).toBe("");
    });

    it("reads the weekday of a server date as cron counts it", () => {
        expect(cronWeekday(new Date("2026-09-28T00:00:00Z"))).toBe(1); // a Monday
        expect(cronWeekday(new Date("2026-10-04T00:00:00Z"))).toBe(7); // a Sunday
    });
});
