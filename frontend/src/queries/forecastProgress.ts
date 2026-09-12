import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { skipToken, useQuery, type QueryClient, type QueryKey } from "@tanstack/vue-query";

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
