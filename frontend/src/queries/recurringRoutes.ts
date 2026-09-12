import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { RecurringRoutesApi } from "@norain/api/apis";
import type { RecurringRouteIn } from "@norain/api/models";

import { entitlementKeys } from "@/queries/entitlements";

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

export function useRecurringRouteForecast(
    id: MaybeRefOrGetter<string | null | undefined>,
    date: MaybeRefOrGetter<string>,
    time: MaybeRefOrGetter<string>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const queryKey = computed(() => recurringRouteKeys.forecast(toValue(id), toValue(date), toValue(time)));
    return useQuery({
        queryKey,
        queryFn: () =>
            api.coreApiRecurringRouteRouteForecast({
                routeId: toValue(id) ?? "",
                date: toValue(date),
                time: toValue(time),
            }),
        enabled: () => toValue(enabled),
        staleTime: 5 * 60 * 1000,
    });
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
