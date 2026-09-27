import { describe, expect, it } from "vitest";
import { nextTick, ref } from "vue";
import { useRoutePosition, type PositionedForecast } from "../useRoutePosition";

// A straight line east with a sample at the start, the middle and the end.
function forecast(jobId = "job"): PositionedForecast {
    const sample = (lon: number, elapsedS: number) => ({
        lat: 47,
        lon,
        elapsedS,
        eta: "2026-09-27T08:00:00",
        rainMm: 0,
        temp: 12,
        weatherDesc: "",
    });
    return {
        jobId,
        line: [
            [8, 47],
            [8.5, 47],
            [9, 47],
        ],
        totalSeconds: 3600,
        samples: [sample(8, 0), sample(8.5, 1200), sample(9, 3600)],
    };
}

describe("useRoutePosition", () => {
    it("selects any place along the route and the sample nearest to it", () => {
        const { position, selectedSample, selectPosition } = useRoutePosition(() => forecast());
        expect(position.value).toBe(0);
        selectPosition(0.3);
        expect(position.value).toBe(0.3);
        expect(selectedSample.value).toBe(1);
        selectPosition(0.9);
        expect(selectedSample.value).toBe(2);
    });

    it("moves the position onto a picked sample", () => {
        const { position, selectSample } = useRoutePosition(() => forecast());
        selectSample(1);
        expect(position.value).toBeCloseTo(0.5, 3);
    });

    it("converts between the position and the charts' ride time", () => {
        const { positionMinutes, selectPosition, selectMinutes, position } = useRoutePosition(() => forecast());
        // The first half takes 20 min, the second 40.
        selectPosition(0.25);
        expect(positionMinutes.value).toBeCloseTo(10, 1);
        selectMinutes(40);
        expect(position.value).toBeCloseTo(0.75, 2);
    });

    it("starts over at the first sample for another forecast", async () => {
        const current = ref(forecast("a"));
        const { position, selectedSample, selectPosition } = useRoutePosition(() => current.value);
        selectPosition(0.8);
        current.value = forecast("b");
        await nextTick();
        expect(selectedSample.value).toBe(0);
        expect(position.value).toBe(0);
    });
});
