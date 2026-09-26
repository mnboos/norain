import { computed, watch, type MaybeRefOrGetter, toValue } from "vue";
import { useInfiniteQuery, useQuery } from "@tanstack/vue-query";
import { useDocumentVisibility } from "@vueuse/core";
import {
    SystemApi,
    CoreApiSystemCoverageKindEnum,
    type SystemApiCoreApiSystemMapFeaturesRequest,
} from "@norain/api/apis";
import type { SystemFeature } from "@norain/api/models";

const api = new SystemApi();
export const systemKey = ["system"] as const;

export function useSystemSummary(enabled: MaybeRefOrGetter<boolean>) {
    const visibility = useDocumentVisibility();
    return useQuery({
        queryKey: [...systemKey, "summary"],
        queryFn: ({ signal }) => api.coreApiSystemSummary({ signal }),
        enabled,
        refetchInterval: () => (visibility.value === "visible" ? 60_000 : false),
        retry: false,
    });
}

export function useSystemLayer(
    params: MaybeRefOrGetter<SystemApiCoreApiSystemMapFeaturesRequest>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const visibility = useDocumentVisibility();
    const query = useInfiniteQuery({
        queryKey: computed(() => [...systemKey, "map", toValue(params)]),
        queryFn: ({ signal, pageParam }) =>
            api.coreApiSystemMapFeatures({ ...toValue(params), offset: pageParam }, { signal }),
        initialPageParam: 0,
        getNextPageParam: page => page.nextOffset ?? undefined,
        enabled,
        retry: false,
        refetchInterval: () => (visibility.value === "visible" ? 60_000 : false),
    });
    watch(
        [query.hasNextPage, query.isFetching, query.isError, () => toValue(enabled)],
        ([next, fetching, error, active]) => {
            if (active && next && !fetching && !error) void query.fetchNextPage();
        },
    );
    return {
        query,
        items: computed(() => (toValue(enabled) ? (query.data.value?.pages.flatMap(page => page.items) ?? []) : [])),
        total: computed(() => (toValue(enabled) ? (query.data.value?.pages[0]?.total ?? 0) : 0)),
    };
}

export function useSystemCoverage(
    selected: MaybeRefOrGetter<SystemFeature | null>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const visibility = useDocumentVisibility();
    return useQuery({
        queryKey: computed(() => [...systemKey, "coverage", toValue(selected)?.kind, toValue(selected)?.id]),
        enabled: () => toValue(enabled) && ["route", "stage"].includes(toValue(selected)?.kind ?? ""),
        retry: false,
        queryFn: ({ signal }) => {
            const item = toValue(selected);
            if (!item) throw new Error("Select a route first.");
            return api.coreApiSystemCoverage(
                {
                    kind:
                        toValue(selected)?.kind === "route"
                            ? CoreApiSystemCoverageKindEnum.Route
                            : CoreApiSystemCoverageKindEnum.Stage,
                    itemId: item.id,
                },
                { signal },
            );
        },
        refetchInterval: () => (visibility.value === "visible" ? 60_000 : false),
    });
}

export function useSystemCellHistory(
    selected: MaybeRefOrGetter<SystemFeature | null>,
    offset: MaybeRefOrGetter<number>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const visibility = useDocumentVisibility();
    return useQuery({
        queryKey: computed(() => [
            ...systemKey,
            "history",
            toValue(selected)?.lat,
            toValue(selected)?.lon,
            toValue(offset),
        ]),
        enabled: () => toValue(enabled) && toValue(selected)?.lat != null && toValue(selected)?.lon != null,
        retry: false,
        queryFn: ({ signal }) => {
            const item = toValue(selected);
            if (item?.lat == null || item.lon == null) throw new Error("Select a cell first.");
            return api.coreApiSystemCellHistory({ lat: item.lat, lon: item.lon, offset: toValue(offset) }, { signal });
        },
        refetchInterval: () => (visibility.value === "visible" ? 60_000 : false),
    });
}

export function useSystemJobs(offset: MaybeRefOrGetter<number>, enabled: MaybeRefOrGetter<boolean>) {
    const visibility = useDocumentVisibility();
    return useQuery({
        queryKey: computed(() => [...systemKey, "jobs", toValue(offset)]),
        enabled,
        retry: false,
        queryFn: ({ signal }) => api.coreApiSystemJobs({ offset: toValue(offset) }, { signal }),
        refetchInterval: () => (visibility.value === "visible" ? 60_000 : false),
    });
}
