import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { RecurringRoutesApi } from "@norain/api/apis";
import type { RecurringRouteIn } from "@norain/api/models";

import { entitlementKeys } from "@/queries/entitlements";
import { reportForecastProgress, useForecastProgress } from "@/queries/forecastProgress";
import { awaitForecastJob } from "@/services/forecastJob";

const api = new RecurringRoutesApi();

export const recurringRouteKeys = {
    all: ["recurringRoutes"] as const,
    lists: () => [...recurringRouteKeys.all, "list"] as const,
    detail: (id: string | null | undefined) => [...recurringRouteKeys.all, "detail", id] as const,
    forecast: (id: string | null | undefined, date: string, time: string) =>
        [...recurringRouteKeys.detail(id), "forecast", date, time] as const,
};

export function useRecurringRoutes() {
    return useQuery({
        queryKey: recurringRouteKeys.lists(),
        queryFn: () => api.coreApiRecurringRouteListRoutes(),
        refetchInterval: 60_000,
        staleTime: 30_000,
    });
}

export function useRecurringRoute(
    id: MaybeRefOrGetter<string | null | undefined>,
    refetchInterval?: MaybeRefOrGetter<number | false>,
) {
    const queryKey = computed(() => recurringRouteKeys.detail(toValue(id)));
    return useQuery({
        queryKey,
        queryFn: () => api.coreApiRecurringRouteGetRoute({ routeId: toValue(id) ?? "" }),
        enabled: () => !!toValue(id),
        refetchInterval: () => (refetchInterval === undefined ? false : toValue(refetchInterval)),
    });
}

/**
 * The forecast for one departure of a saved route.
 *
 * The endpoint starts a background job rather than computing the forecast inline, so the
 * query resolves once the job reports `done`. `progress` counts grid cells as they are
 * fetched, for a determinate progress indicator while that happens.
 */
export function useRecurringRouteForecast(
    id: MaybeRefOrGetter<string | null | undefined>,
    date: MaybeRefOrGetter<string>,
    time: MaybeRefOrGetter<string>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const queryKey = computed(() => recurringRouteKeys.forecast(toValue(id), toValue(date), toValue(time)));
    const query = useQuery({
        queryKey,
        queryFn: async ({ client, queryKey: key, signal }) => {
            const job = await api.coreApiRecurringRouteRouteForecast({
                routeId: toValue(id) ?? "",
                date: toValue(date),
                time: toValue(time),
            });
            return await awaitForecastJob(job, reportForecastProgress(client, key), signal);
        },
        enabled: () => toValue(enabled),
        staleTime: 5 * 60 * 1000,
    });
    return Object.assign(query, { progress: useForecastProgress(queryKey) });
}

async function invalidateRouteLists(queryClient: ReturnType<typeof useQueryClient>) {
    await Promise.all([
        queryClient.invalidateQueries({ queryKey: recurringRouteKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: entitlementKeys.all }),
    ]);
}

export function useCreateRecurringRoute() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: RecurringRouteIn) => api.coreApiRecurringRouteCreateRoute({ recurringRouteIn: data }),
        onSuccess: () => invalidateRouteLists(queryClient),
        onError: () => queryClient.invalidateQueries({ queryKey: entitlementKeys.all }),
    });
}

export function useDeleteRecurringRoute() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (id: string) => api.coreApiRecurringRouteDeleteRoute({ routeId: id }),
        onSuccess: () => invalidateRouteLists(queryClient),
    });
}
