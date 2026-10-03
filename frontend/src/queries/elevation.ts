import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { ElevationApi } from "@norain/api/apis";
import type { RouteForecastOut } from "@norain/api/models";

const api = new ElevationApi();

export function useForecastElevation(forecast: MaybeRefOrGetter<RouteForecastOut>) {
    return useQuery({
        queryKey: computed(() => ["elevation", "forecast", toValue(forecast).jobId, toValue(forecast).version]),
        enabled: () => !!toValue(forecast).jobId,
        queryFn: ({ signal }) => api.coreApiElevationForecastElevation({ jobId: toValue(forecast).jobId }, { signal }),
        staleTime: 60 * 60 * 1000,
        retry: 1,
    });
}
