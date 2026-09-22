import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/vue-query";
import { RecurringRoutesApi } from "@norain/api/apis";
import type {
    RecurringRouteIn,
    RecurringRouteOut,
    RouteForecastOut,
    RoutePreviewIn,
    RoutePreviewOut,
} from "@norain/api/models";

import { entitlementKeys } from "@/queries/entitlements";
import { reportForecastProgress, useForecastProgress } from "@/queries/forecastProgress";
import { measureForecastLoad } from "@/services/telemetry";
import { awaitForecastJob } from "@/services/forecastJob";

const api = new RecurringRoutesApi();

export const recurringRouteKeys = {
    all: ["recurringRoutes"] as const,
    lists: () => [...recurringRouteKeys.all, "list"] as const,
    detail: (id: string | null | undefined) => [...recurringRouteKeys.all, "detail", id] as const,
    forecast: (id: string | null | undefined, date: string, time: string, before?: number, after?: number) =>
        [...recurringRouteKeys.detail(id), "forecast", date, time, before, after] as const,
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
 * The date and time the route page asks a forecast for: the route's next departure.
 *
 * The page and the prefetch must build the exact same query key, so both read it here.
 * `nextDeparture` is Swiss local time with its offset ("2026-09-18T07:30:00+02:00"). It is
 * split as a string, never parsed: the server pre-builds the forecast for that exact string,
 * and joins date and time back into it to find the job. The offset also keeps the repeated
 * hour on the autumn DST change unambiguous.
 */
export function nextDepartureParts(route: Pick<RecurringRouteOut, "nextDeparture">): { date: string; time: string } {
    const next = route.nextDeparture;
    if (!next) return { date: "", time: "" };
    return { date: next.slice(0, 10), time: next.slice(11) };
}

/** How long a finished forecast is served from the cache before it is asked for again. */
const FORECAST_STALE_TIME = 5 * 60 * 1000;

/** Keep a prefetched forecast around long enough for the user to get to it. */
const PREFETCH_GC_TIME = 30 * 60 * 1000;

/** Tells the server the user has not opened this route, so it is not counted as a view. */
const PREFETCH_HEADERS = { "X-NoRain-Prefetch": "1" };

/** Key and fetch for one departure's forecast, shared by the page and the prefetch. */
function recurringRouteForecastOptions(
    id: MaybeRefOrGetter<string | null | undefined>,
    date: MaybeRefOrGetter<string>,
    time: MaybeRefOrGetter<string>,
    before: MaybeRefOrGetter<number | undefined>,
    after: MaybeRefOrGetter<number | undefined>,
    source: "page" | "prefetch",
) {
    // `source` only labels the request and its telemetry; the forecast is the same.
    // eslint-disable-next-line @tanstack/query/exhaustive-deps
    return {
        queryKey: recurringRouteKeys.forecast(
            toValue(id),
            toValue(date),
            toValue(time),
            toValue(before),
            toValue(after),
        ),
        queryFn: async ({
            client,
            queryKey: key,
            signal,
        }: {
            client: QueryClient;
            queryKey: QueryKey;
            signal: AbortSignal;
        }) =>
            await measureForecastLoad(
                async onDelivery => {
                    const request = {
                        routeId: toValue(id) ?? "",
                        date: toValue(date),
                        time: toValue(time),
                        departureFlexBeforeMinutes: toValue(before),
                        departureFlexAfterMinutes: toValue(after),
                    };
                    const job =
                        source === "prefetch"
                            ? await api.coreApiRecurringRouteRouteForecast(request, { headers: PREFETCH_HEADERS })
                            : await api.coreApiRecurringRouteRouteForecast(request);
                    return await awaitForecastJob(job, reportForecastProgress(client, key), signal, onDelivery);
                },
                { feature: "route", "route.id": toValue(id) ?? "", prefetch: source === "prefetch" },
                signal,
            ),
        staleTime: FORECAST_STALE_TIME,
    };
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
    before?: MaybeRefOrGetter<number | undefined>,
    after?: MaybeRefOrGetter<number | undefined>,
) {
    const queryKey = computed(() => recurringRouteForecastOptions(id, date, time, before, after, "page").queryKey);
    const query = useQuery({
        queryKey,
        queryFn: context => recurringRouteForecastOptions(id, date, time, before, after, "page").queryFn(context),
        enabled: () => toValue(enabled),
        refetchInterval: () => (toValue(before) || toValue(after) ? 60_000 : false),
        staleTime: FORECAST_STALE_TIME,
    });
    return Object.assign(query, { progress: useForecastProgress(queryKey) });
}

/**
 * Load the forecast the route page will ask for first, before the user opens it.
 *
 * Builds the same key as `RouteDetailPanel`: the next departure and the route's saved
 * departure window. Resolves to `undefined` when the forecast could not be loaded - a
 * prefetch that fails must not show anywhere; the page will simply try again.
 */
export async function prefetchRecurringRouteForecast(
    client: QueryClient,
    route: Pick<RecurringRouteOut, "id" | "nextDeparture" | "departureFlexBeforeMinutes" | "departureFlexAfterMinutes">,
): Promise<RouteForecastOut | undefined> {
    const { date, time } = nextDepartureParts(route);
    const options = recurringRouteForecastOptions(
        route.id,
        date,
        time,
        route.departureFlexBeforeMinutes ?? 0,
        route.departureFlexAfterMinutes ?? 0,
        "prefetch",
    );
    try {
        return await client.query({ ...options, gcTime: PREFETCH_GC_TIME });
    } catch {
        return undefined;
    }
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

export function useUpdateRecurringRoute() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ id, data }: { id: string; data: RecurringRouteIn }) =>
            api.coreApiRecurringRouteUpdateRoute({ routeId: id, recurringRouteIn: data }),
        onSuccess: async route => {
            queryClient.setQueryData(recurringRouteKeys.detail(route.id), route);
            // A reshaped route has no geometry until the server rebuilds it; its cached
            // forecasts describe the old line, so mark them stale for when it is back.
            if (!route.hasGeometry) {
                await queryClient.invalidateQueries({ queryKey: recurringRouteKeys.detail(route.id), exact: false });
            }
            await invalidateRouteLists(queryClient);
        },
    });
}

/**
 * The line through start, via points and destination, for the route editor. Not a query:
 * the editor calls it once per drag and cancels the previous call through `signal`.
 */
export function previewRoute(data: RoutePreviewIn, signal: AbortSignal): Promise<RoutePreviewOut> {
    return api.coreApiRecurringRouteRoutePreview({ routePreviewIn: data }, { signal });
}
