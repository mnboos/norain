import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from "@tanstack/vue-query";
import { JourneysApi } from "@norain/api/apis";
import type { JourneyIn } from "@norain/api/models";

import { reportForecastProgress, useForecastProgress } from "@/queries/forecastProgress";
import { awaitForecastJob } from "@/services/forecastJob";

const api = new JourneysApi();

export const journeyKeys = {
    all: ["journeys"] as const,
    lists: () => [...journeyKeys.all, "list"] as const,
    detail: (id: string | null | undefined) => [...journeyKeys.all, "detail", id] as const,
    stageForecast: (id: string | null | undefined, stageId: string | null | undefined) =>
        [...journeyKeys.detail(id), "stage", stageId, "forecast"] as const,
    stagePois: (id: string | null | undefined, stageId: string | null | undefined, categories: string, lodging: boolean) =>
        [...journeyKeys.detail(id), "stage", stageId, "pois", categories, lodging] as const,
};

/** Still working on the server: the plan itself, or a stage forecast it started on read. */
export function journeyIsBusy(
    journey:
        | { planStatus: string; days?: { stages?: { forecastStatus?: string | null }[] }[] }
        | undefined,
): boolean {
    if (!journey) return false;
    if (journey.planStatus !== "done" && journey.planStatus !== "failed") return true;
    return (journey.days ?? []).some(day =>
        (day.stages ?? []).some(stage => stage.forecastStatus && !["done", "failed"].includes(stage.forecastStatus)),
    );
}

export function useJourneys() {
    return useQuery({
        queryKey: journeyKeys.lists(),
        queryFn: () => api.coreApiJourneyListJourneys(),
        staleTime: 30_000,
    });
}

/**
 * One journey with its plan. Polls while the plan or a stage forecast is still running: the
 * ranking of a day's alternatives is computed when the journey is read, so it fills in as
 * the stage forecasts finish.
 */
export function useJourney(id: MaybeRefOrGetter<string | null | undefined>) {
    const queryKey = computed(() => journeyKeys.detail(toValue(id)));
    return useQuery({
        queryKey,
        queryFn: () => api.coreApiJourneyGetJourney({ journeyId: toValue(id) ?? "" }),
        enabled: () => !!toValue(id),
        refetchInterval: query => (journeyIsBusy(query.state.data) ? 3000 : false),
    });
}

/** The full forecast of one stage, the same payload a saved route's forecast has. */
export function useJourneyStageForecast(
    id: MaybeRefOrGetter<string | null | undefined>,
    stageId: MaybeRefOrGetter<string | null | undefined>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const queryKey = computed(() => journeyKeys.stageForecast(toValue(id), toValue(stageId)));
    const query = useQuery({
        queryKey,
        queryFn: ({ client, queryKey: key, signal }) =>
            fetchStageForecast(client, key, toValue(id) ?? "", toValue(stageId) ?? "", signal),
        enabled: () => !!toValue(id) && !!toValue(stageId) && toValue(enabled),
        staleTime: STAGE_FORECAST_STALE_MS,
    });
    return Object.assign(query, { progress: useForecastProgress(queryKey) });
}

/**
 * The forecasts of several stages, under the same keys as `useJourneyStageForecast`, so a
 * variant fetched here opens at once when it is picked.
 */
export function useJourneyStageForecasts(
    id: MaybeRefOrGetter<string | null | undefined>,
    stageIds: MaybeRefOrGetter<string[]>,
) {
    return useQueries({
        queries: computed(() =>
            toValue(stageIds).map(stageId => ({
                queryKey: journeyKeys.stageForecast(toValue(id), stageId),
                queryFn: ({ client, queryKey: key, signal }: { client: QueryClient; queryKey: readonly unknown[]; signal: AbortSignal }) =>
                    fetchStageForecast(client, key, toValue(id) ?? "", stageId, signal),
                enabled: !!toValue(id),
                staleTime: STAGE_FORECAST_STALE_MS,
            })),
        ),
    });
}

const STAGE_FORECAST_STALE_MS = 5 * 60 * 1000;

async function fetchStageForecast(
    client: QueryClient,
    key: readonly unknown[],
    journeyId: string,
    stageId: string,
    signal: AbortSignal,
) {
    const job = await api.coreApiJourneyJourneyStageForecast({ journeyId, stageId });
    return await awaitForecastJob(job, reportForecastProgress(client, key), signal);
}

/**
 * POIs in the area of several stages (a day's variants), for the map, one query per stage;
 * with `lodging`, also where the day could end. The data is in the order of `stageIds`.
 */
export function useJourneyStagesPois(
    id: MaybeRefOrGetter<string | null | undefined>,
    stageIds: MaybeRefOrGetter<string[]>,
    categories: MaybeRefOrGetter<string[]>,
    lodging: MaybeRefOrGetter<boolean> = false,
) {
    const joined = computed(() => [...toValue(categories)].sort().join(","));
    return useQueries({
        queries: computed(() =>
            toValue(stageIds).map(stageId => ({
                queryKey: journeyKeys.stagePois(toValue(id), stageId, joined.value, toValue(lodging)),
                queryFn: () =>
                    api.coreApiJourneyJourneyStagePois({
                        journeyId: toValue(id) ?? "",
                        stageId,
                        categories: joined.value,
                        lodging: toValue(lodging),
                    }),
                enabled: !!toValue(id) && (joined.value.length > 0 || toValue(lodging)),
                staleTime: 30 * 60 * 1000,
            })),
        ),
    });
}

export function useCreateJourney() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: JourneyIn) => api.coreApiJourneyCreateJourney({ journeyIn: data }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: journeyKeys.lists() }),
    });
}

export function useUpdateJourney() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ id, data }: { id: string; data: JourneyIn }) =>
            api.coreApiJourneyUpdateJourney({ journeyId: id, journeyIn: data }),
        onSuccess: async journey => {
            // A re-plan replaces every day and stage: nothing cached below the journey still fits.
            await queryClient.invalidateQueries({ queryKey: journeyKeys.detail(journey.id) });
            await queryClient.invalidateQueries({ queryKey: journeyKeys.lists() });
        },
    });
}

export function useReplanJourney() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (id: string) => api.coreApiJourneyReplanJourney({ journeyId: id }),
        onSuccess: journey => queryClient.invalidateQueries({ queryKey: journeyKeys.detail(journey.id) }),
    });
}

export function useDeleteJourney() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (id: string) => api.coreApiJourneyDeleteJourney({ journeyId: id }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: journeyKeys.lists() }),
    });
}
