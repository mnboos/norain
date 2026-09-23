import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { skipToken, useQuery, type QueryClient, type QueryKey } from "@tanstack/vue-query";

import type { RouteForecastOut } from "@norain/api/models";

import type { ForecastProgress } from "@/services/forecastJob";

/**
 * How far a forecast job's fan-out has got, kept in the query cache beside the forecast.
 *
 * The forecast query's function writes it and the page reads it. Keeping it in the cache,
 * rather than in a ref that the query function closes over, leaves that function depending
 * on nothing but its own key.
 */
export const forecastProgressKey = (forecastKey: QueryKey) => ["forecastProgress", ...forecastKey] as const;

/** The progress callback for one forecast query, writing into the cache under its key. */
export function reportForecastProgress(client: QueryClient, forecastKey: QueryKey) {
    return (progress: ForecastProgress) => {
        client.setQueryData(forecastProgressKey(forecastKey), progress);
    };
}

/**
 * The callback that shows a refreshing job's previous result under the forecast query's own
 * key, while that query is still fetching. The query keeps `isFetching` until the fresh result
 * replaces it, and keeps the stale data (with `error` set) if the refresh fails.
 */
export function reportStaleForecast(client: QueryClient, forecastKey: QueryKey) {
    return (result: RouteForecastOut) => {
        // Dated by when it was computed, not now: if the refresh is cancelled (the page left,
        // another variant picked), the next mount must still see it as stale and refetch.
        client.setQueryData(forecastKey, result, { updatedAt: Date.parse(result.computedAt ?? "") || 0 });
    };
}

/** The latest progress of the forecast query with this key, or null before the first update. */
export function useForecastProgress(forecastKey: MaybeRefOrGetter<QueryKey>) {
    const query = useQuery<ForecastProgress>({
        queryKey: computed(() => forecastProgressKey(toValue(forecastKey))),
        // Never fetched: only the forecast query writes this entry.
        queryFn: skipToken,
        staleTime: Infinity,
    });
    return computed(() => query.data.value ?? null);
}
