import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { PlacesApi } from "@norain/api/apis";

const api = new PlacesApi();

export interface SearchLocation {
    zoom: number;
    lat: number;
    lon: number;
}

export const placeKeys = {
    all: ["places"] as const,
    search: (query: string, location: SearchLocation) => [...placeKeys.all, "search", query, location] as const,
};

export function usePlaceSearch(
    query: MaybeRefOrGetter<string>,
    location: MaybeRefOrGetter<SearchLocation | undefined>,
) {
    const queryKey = computed(() => placeKeys.search(toValue(query), toValue(location) ?? { zoom: 0, lat: 0, lon: 0 }));
    return useQuery({
        queryKey,
        enabled: () => toValue(query).length > 2 && !!toValue(location),
        queryFn: () => {
            const position = toValue(location);
            if (!position) return [];
            return api.coreApiPlacesSearch({ query: toValue(query), ...position });
        },
        initialData: [],
    });
}
