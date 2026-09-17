import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DepartureComparison as Comparison } from "@norain/api/models";
import DepartureComparison from "../DepartureComparison.vue";

function comparison(): Comparison {
    return {
        requestedTime: "2030-06-01T08:00:00+02:00",
        windowStart: "2030-06-01T07:45:00+02:00",
        windowEnd: "2030-06-01T08:15:00+02:00",
        recommendedTime: "2030-06-01T08:15:00+02:00",
        explanation: "Weniger Regen während deiner Fahrt.",
        candidates: ["07:45", "08:00", "08:15"].map((time, i) => ({
            departureTime: `2030-06-01T${time}:00+02:00`,
            arrivalTime: `2030-06-01T${i === 0 ? "08:45" : i === 1 ? "09:00" : "09:15"}:00+02:00`,
            available: i > 0,
            rideScore: i === 0 ? null : 0.3 - i * 0.1,
            rideLabel: "gut",
        })),
    };
}

describe("DepartureComparison", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2030-06-01T05:00:00Z"));
    });
    afterEach(() => vi.useRealTimers());

    it("shows requested, recommended, and selected states with text and accessible buttons", async () => {
        const wrapper = mount(DepartureComparison, { props: { comparison: comparison() } });
        const buttons = wrapper.findAll(".departure-option");
        expect(buttons[0]?.attributes("disabled")).toBeDefined();
        expect(buttons[1]?.attributes("aria-pressed")).toBe("true");
        expect(buttons[1]?.text()).toContain("Gewünscht");
        expect(buttons[2]?.text()).toContain("Empfohlen");
        expect(wrapper.text()).toContain("Weniger Regen");
        expect(wrapper.text()).toContain("15-Minuten-Schritten");
        await wrapper.get(".departure-option:nth-child(3)").trigger("click");
        expect(wrapper.emitted("select")?.[0]).toEqual(["2030-06-01T08:15:00+02:00"]);
        wrapper.unmount();
    });

    it("keeps the original comparison window when applying and resetting a selection", async () => {
        const data = comparison();
        const wrapper = mount(DepartureComparison, { props: { comparison: data, selectedTime: data.recommendedTime } });
        expect(wrapper.findAll(".departure-option")[2]?.attributes("aria-pressed")).toBe("true");
        expect(wrapper.findAll(".departure-option")[1]?.text()).toContain("Gewünscht");
        await wrapper.get(".reset-time").trigger("click");
        expect(wrapper.emitted("reset")).toHaveLength(1);
        expect(data.windowStart).toBe("2030-06-01T07:45:00+02:00");
        await wrapper.get(".departure-option:nth-child(2)").trigger("click");
        expect(wrapper.emitted("reset")).toHaveLength(2);
        wrapper.unmount();
    });

    it("rechecks the clock before applying a cached suggestion", async () => {
        const wrapper = mount(DepartureComparison, { props: { comparison: comparison() } });
        vi.setSystemTime(new Date("2030-06-01T09:00:00Z"));
        await wrapper.get(".departure-option:nth-child(3)").trigger("click");
        expect(wrapper.emitted("select")).toBeUndefined();
        await vi.advanceTimersByTimeAsync(15_000);
        expect(wrapper.findAll(".departure-option").every(b => b.attributes("disabled") !== undefined)).toBe(true);
        expect(wrapper.text()).not.toContain("Empfohlen");
        wrapper.unmount();
    });

    it("distinguishes repeated daylight-saving times by UTC offset", () => {
        const data = comparison();
        data.candidates = ["+02:00", "+01:00"].map(offset => ({
            departureTime: `2030-10-27T02:15:00${offset}`,
            arrivalTime: `2030-10-27T02:45:00${offset}`,
            available: true,
            rideScore: 0,
            rideLabel: "sehr gut",
        }));
        const wrapper = mount(DepartureComparison, { props: { comparison: data } });
        const labels = wrapper.findAll(".departure-option strong").map(b => b.text());
        expect(labels[0]).not.toEqual(labels[1]);
        expect(labels.every(l => l.includes("02:15"))).toBe(true);
        wrapper.unmount();
    });
});
