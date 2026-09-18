import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { RouteWeatherApi } from "@norain/api/apis";
import type { PlacesSearchResult } from "@norain/api/models";

import { reportForecastProgress, useForecastProgress } from "@/queries/forecastProgress";
import { measureForecastLoad } from "@/services/telemetry";
import { awaitForecastJob } from "@/services/forecastJob";

const api = new RouteWeatherApi();

export const routeWeatherKeys = {
    all: ["routeWeather"] as const,
    forecast: (
        start: PlacesSearchResult,
        destination: PlacesSearchResult | undefined,
        profile: string,
        departure: string,
        before = 0,
        after = 0,
    ) =>
        [
            ...routeWeatherKeys.all,
            "forecast",
            start.geometry.coordinates,
            destination?.geometry.coordinates,
            profile,
            departure,
            before,
            after,
        ] as const,
};

export function useRouteWeather(
    start: MaybeRefOrGetter<PlacesSearchResult>,
    destination: MaybeRefOrGetter<PlacesSearchResult | undefined>,
    profile: MaybeRefOrGetter<string>,
    departure: MaybeRefOrGetter<string>,
    before: MaybeRefOrGetter<number> = 0,
    after: MaybeRefOrGetter<number> = 0,
    enabled: MaybeRefOrGetter<boolean> = true,
) {
    const queryKey = computed(() =>
        routeWeatherKeys.forecast(
            toValue(start),
            toValue(destination),
            toValue(profile),
            toValue(departure),
            toValue(before),
            toValue(after),
        ),
    );
    const query = useQuery({
        queryKey,
        enabled: () => !!toValue(destination) && toValue(enabled),
        refetchInterval: () => (toValue(before) || toValue(after) ? 60_000 : false),
        // Routing and every provider fetch happen on workers, so this resolves when the
        // job does rather than blocking a request for the whole fan-out.
        queryFn: async ({ client, queryKey: key, signal }) => {
            const from = toValue(start).geometry.coordinates;
            const destinationValue = toValue(destination);
            if (!destinationValue) throw new Error("A destination is required.");
            const to = destinationValue.geometry.coordinates;
            return await measureForecastLoad(
                async onDelivery => {
                    const job = await api.coreApiRouteWeatherRouteWeather({
                        startLat: from[1] ?? 0,
                        startLon: from[0] ?? 0,
                        destLat: to[1] ?? 0,
                        destLon: to[0] ?? 0,
                        profile: toValue(profile),
                        departureTime: toValue(departure),
                        departureFlexBeforeMinutes: toValue(before),
                        departureFlexAfterMinutes: toValue(after),
                    });
                    return await awaitForecastJob(job, reportForecastProgress(client, key), signal, onDelivery);
                },
                {
                    feature: "adhoc",
                    profile: toValue(profile),
                    start_lat: from[1] ?? 0,
                    start_lon: from[0] ?? 0,
                    dest_lat: to[1] ?? 0,
                    dest_lon: to[0] ?? 0,
                },
                signal,
            );
        },
        staleTime: 5 * 60 * 1000,
    });
    return Object.assign(query, { progress: useForecastProgress(queryKey) });
}
