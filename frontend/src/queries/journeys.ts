import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
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
    stagePois: (id: string | null | undefined, stageId: string | null | undefined, categories: string) =>
        [...journeyKeys.detail(id), "stage", stageId, "pois", categories] as const,
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
        queryFn: async ({ client, queryKey: key, signal }) => {
            const job = await api.coreApiJourneyJourneyStageForecast({
                journeyId: toValue(id) ?? "",
                stageId: toValue(stageId) ?? "",
            });
            return await awaitForecastJob(job, reportForecastProgress(client, key), signal);
        },
        enabled: () => !!toValue(id) && !!toValue(stageId) && toValue(enabled),
        staleTime: 5 * 60 * 1000,
    });
    return Object.assign(query, { progress: useForecastProgress(queryKey) });
}

/** POIs on the way along a stage, for the map. */
export function useJourneyStagePois(
    id: MaybeRefOrGetter<string | null | undefined>,
    stageId: MaybeRefOrGetter<string | null | undefined>,
    categories: MaybeRefOrGetter<string[]>,
) {
    const joined = computed(() => [...toValue(categories)].sort().join(","));
    return useQuery({
        queryKey: computed(() => journeyKeys.stagePois(toValue(id), toValue(stageId), joined.value)),
        queryFn: () =>
            api.coreApiJourneyJourneyStagePois({
                journeyId: toValue(id) ?? "",
                stageId: toValue(stageId) ?? "",
                categories: joined.value,
            }),
        enabled: () => !!toValue(id) && !!toValue(stageId) && joined.value.length > 0,
        staleTime: 30 * 60 * 1000,
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
