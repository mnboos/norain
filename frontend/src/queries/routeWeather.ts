import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { RouteWeatherApi } from "@norain/api/apis";
import type { PlacesSearchResult } from "@norain/api/models";

const api = new RouteWeatherApi();

export const routeWeatherKeys = {
    all: ["routeWeather"] as const,
    forecast: (start: PlacesSearchResult, destination: PlacesSearchResult | undefined, profile: string, departure: string) =>
        [...routeWeatherKeys.all, "forecast", start.geometry.coordinates, destination?.geometry.coordinates, profile, departure] as const,
};

export function useRouteWeather(
    start: MaybeRefOrGetter<PlacesSearchResult>,
    destination: MaybeRefOrGetter<PlacesSearchResult | undefined>,
    profile: MaybeRefOrGetter<string>,
    departure: MaybeRefOrGetter<string>,
) {
    const queryKey = computed(() =>
        routeWeatherKeys.forecast(toValue(start), toValue(destination), toValue(profile), toValue(departure)),
    );
    return useQuery({
        queryKey,
        enabled: () => !!toValue(destination),
        queryFn: () => {
            const from = toValue(start).geometry.coordinates;
            const destinationValue = toValue(destination);
            if (!destinationValue) throw new Error("A destination is required.");
            const to = destinationValue.geometry.coordinates;
            return api.coreApiRouteWeatherRouteWeather({
                startLat: from[1] ?? 0,
                startLon: from[0] ?? 0,
                destLat: to[1] ?? 0,
                destLon: to[0] ?? 0,
                profile: toValue(profile),
                departureTime: toValue(departure),
            });
        },
        staleTime: 5 * 60 * 1000,
    });
}
