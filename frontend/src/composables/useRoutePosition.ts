import { computed, ref, watch } from "vue";
import type { RouteForecastOut } from "@norain/api/models";
import { sampleProgress } from "@/utils/rideQuality";
import { interpolate, nearestIndex } from "@/utils/forecastSelection";

/** The parts of a forecast the position reads. */
export type PositionedForecast = Pick<RouteForecastOut, "jobId" | "line" | "samples" | "totalSeconds">;

/**
 * The selected place on a route, shared by the map, the elevation profile and the forecast
 * charts of one page. `position` is a share (0..1) of the route's distance and can be anywhere
 * along it; `selectedSample` is the forecast sample nearest to it, for the details panel.
 *
 * Sample positions are measured on the forecast's own line (`sampleProgress`); the map pins a
 * finer line to the same samples, so a selected sample shows on its own vertex there too.
 */
export function useRoutePosition(forecast: () => PositionedForecast | undefined) {
    const samplePositions = computed(() => {
        const f = forecast();
        return f ? sampleProgress(f.line, f.samples, f.totalSeconds) : [];
    });
    const sampleMinutes = computed(() => (forecast()?.samples ?? []).map(s => s.elapsedS / 60));

    const position = ref<number | undefined>(undefined);
    const selectedSample = ref(0);

    function selectSample(index: number) {
        selectedSample.value = index;
        position.value = samplePositions.value[index];
    }
    function selectPosition(value: number) {
        position.value = Math.max(0, Math.min(1, value));
        const nearest = nearestIndex(samplePositions.value, position.value);
        if (nearest !== undefined) selectedSample.value = nearest;
    }

    /** The position as ride time in minutes, the forecast charts' x axis. */
    const positionMinutes = computed(() =>
        position.value === undefined
            ? undefined
            : interpolate(position.value, samplePositions.value, sampleMinutes.value),
    );
    function selectMinutes(minutes: number) {
        const value = interpolate(minutes, sampleMinutes.value, samplePositions.value);
        if (value !== undefined) selectPosition(value);
    }

    // Not on every new result: a stale forecast and the fresh one replacing it share the job and
    // the samples' places, so the user's pick stays where it was.
    watch(
        [() => forecast()?.jobId, () => forecast()?.samples.length],
        () => {
            selectSample(0);
        },
        { immediate: true },
    );

    return { position, selectedSample, selectSample, selectPosition, positionMinutes, selectMinutes };
}
