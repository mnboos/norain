import { computed, onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { CoreApiRouteWeatherForecastJobMapDetailDetailEnum as MapDetailParam, RouteWeatherApi } from "@norain/api/apis";
import type { ForecastUncertainty } from "@norain/api/models";

const api = new RouteWeatherApi();

/** How much route line and wind-arrow detail to draw. The job result carries `coarse`; the rest is fetched. */
export type LineDetail = "coarse" | "medium" | "full";

/**
 * The parts of a finished forecast that only some components show.
 *
 * The job result is deliberately slim, so each part is fetched by the component that draws
 * it. Keys carry the result `version` as well as the job id: a job re-run after its cells
 * expire keeps its id, and its parts must not be served from the old run.
 */
export const forecastPartKeys = {
    all: ["forecastParts"] as const,
    job: (jobId: string, version: string) => [...forecastPartKeys.all, jobId, version] as const,
    figures: (jobId: string, version: string) => [...forecastPartKeys.job(jobId, version), "figures"] as const,
    mapDetail: (jobId: string, version: string, detail: LineDetail) =>
        [...forecastPartKeys.job(jobId, version), "mapDetail", detail] as const,
    sampleUncertainty: (jobId: string, version: string, index: number) =>
        [...forecastPartKeys.job(jobId, version), "samples", index, "uncertainty"] as const,
};

/** The detail levels the map-detail endpoint serves; `coarse` comes with the job result instead. */
const MAP_DETAIL_PARAM: Record<Exclude<LineDetail, "coarse">, MapDetailParam> = {
    medium: MapDetailParam.Medium,
    full: MapDetailParam.Full,
};

/** A finished job's result never changes under one version, so a part never goes stale. */
const PART_STALE_TIME = Infinity;

export function useForecastFigures(jobId: MaybeRefOrGetter<string>, version: MaybeRefOrGetter<string>) {
    return useQuery({
        queryKey: computed(() => forecastPartKeys.figures(toValue(jobId), toValue(version))),
        queryFn: () => api.coreApiRouteWeatherForecastJobFigures({ jobId: toValue(jobId) }),
        staleTime: PART_STALE_TIME,
    });
}

/** The route line and felt-wind arrows at one detail level finer than the job result's. */
export function useForecastMapDetail(
    jobId: MaybeRefOrGetter<string | undefined>,
    version: MaybeRefOrGetter<string | undefined>,
    detail: MaybeRefOrGetter<LineDetail>,
) {
    return useQuery({
        queryKey: computed(() =>
            forecastPartKeys.mapDetail(toValue(jobId) ?? "", toValue(version) ?? "", toValue(detail)),
        ),
        queryFn: () => {
            const level = toValue(detail);
            if (level === "coarse") throw new Error("The coarse map detail comes with the job result.");
            return api.coreApiRouteWeatherForecastJobMapDetail({
                jobId: toValue(jobId) ?? "",
                detail: MAP_DETAIL_PARAM[level],
            });
        },
        enabled: () => !!toValue(jobId) && toValue(detail) !== "coarse",
        staleTime: PART_STALE_TIME,
    });
}

/** How long the selected sample must stay put before its breakdown is requested. */
const SAMPLE_DEBOUNCE_MS = 150;

/**
 * One sample's full ensemble spread, with the per-model breakdown.
 *
 * Dragging the sample slider moves through many points quickly, so the index is debounced.
 * `isCurrent` is false while the data still belongs to an earlier point (during the
 * debounce, or while `keepPreviousData` holds the old answer) - callers must not show it
 * as the selected point's.
 */
export function useSampleUncertainty(
    jobId: MaybeRefOrGetter<string>,
    version: MaybeRefOrGetter<string>,
    index: MaybeRefOrGetter<number>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const settledIndex = ref(toValue(index));
    let timer: ReturnType<typeof setTimeout> | undefined;
    watch(
        () => toValue(index),
        value => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                settledIndex.value = value;
            }, SAMPLE_DEBOUNCE_MS);
        },
    );
    onBeforeUnmount(() => {
        clearTimeout(timer);
    });

    const query = useQuery({
        queryKey: computed(() =>
            forecastPartKeys.sampleUncertainty(toValue(jobId), toValue(version), settledIndex.value),
        ),
        // The generated client types the answer as always present, but `null` is a real one:
        // the sample has no spread, or the account's tier has none.
        queryFn: async (): Promise<ForecastUncertainty | null> =>
            api.coreApiRouteWeatherForecastJobSampleUncertainty({
                jobId: toValue(jobId),
                index: settledIndex.value,
            }),
        enabled: () => toValue(enabled),
        placeholderData: keepPreviousData,
        staleTime: PART_STALE_TIME,
    });
    const isCurrent = computed(
        () => settledIndex.value === toValue(index) && query.isSuccess.value && !query.isPlaceholderData.value,
    );
    return { data: query.data, isError: query.isError, isCurrent };
}
