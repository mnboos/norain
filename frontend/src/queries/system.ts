import { computed, watch, type MaybeRefOrGetter, toValue } from "vue";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useDocumentVisibility, useWebSocket } from "@vueuse/core";
import {
    SystemApi,
    CoreApiSystemCoverageKindEnum,
    type SystemApiCoreApiSystemMapFeaturesRequest,
} from "@norain/api/apis";
import type { SystemFeature } from "@norain/api/models";
import { t } from "@/i18n";
import { useBackendHost } from "@/utils";
import { systemQueryAffected } from "@/utils/systemOverview";

const api = new SystemApi();
export const systemKey = ["system"] as const;

/** The server refuses a socket without verified admin access with this close code. */
const REFUSED = 4003;

export function useSystemSummary(enabled: MaybeRefOrGetter<boolean>) {
    return useQuery({
        queryKey: [...systemKey, "summary"],
        queryFn: ({ signal }) => api.coreApiSystemSummary({ signal }),
        enabled,
        retry: false,
    });
}

export function useSystemLayer(
    params: MaybeRefOrGetter<SystemApiCoreApiSystemMapFeaturesRequest>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const query = useInfiniteQuery({
        queryKey: computed(() => [...systemKey, "map", toValue(params)]),
        queryFn: ({ signal, pageParam }) =>
            api.coreApiSystemMapFeatures({ ...toValue(params), offset: pageParam }, { signal }),
        initialPageParam: 0,
        getNextPageParam: page => page.nextOffset ?? undefined,
        enabled,
        retry: false,
        placeholderData: (previousData) => previousData,
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
    return useQuery({
        queryKey: computed(() => [...systemKey, "coverage", toValue(selected)?.kind, toValue(selected)?.id]),
        enabled: () => toValue(enabled) && ["route", "stage"].includes(toValue(selected)?.kind ?? ""),
        retry: false,
        queryFn: ({ signal }) => {
            const item = toValue(selected);
            if (!item) throw new Error(t("system.selectRoute"));
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
    });
}

export function useSystemCellHistory(
    selected: MaybeRefOrGetter<SystemFeature | null>,
    offset: MaybeRefOrGetter<number>,
    enabled: MaybeRefOrGetter<boolean>,
) {
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
            if (item?.lat == null || item.lon == null) throw new Error(t("system.selectCell"));
            return api.coreApiSystemCellHistory({ lat: item.lat, lon: item.lon, offset: toValue(offset) }, { signal });
        },
    });
}

export function useSystemJobs(offset: MaybeRefOrGetter<number>, enabled: MaybeRefOrGetter<boolean>) {
    return useQuery({
        queryKey: computed(() => [...systemKey, "jobs", toValue(offset)]),
        enabled,
        retry: false,
        queryFn: ({ signal }) => api.coreApiSystemJobs({ offset: toValue(offset) }, { signal }),
    });
}

/** The requesting browser's own recognition result; no change notice touches it. */
export function useSystemBrowser(enabled: MaybeRefOrGetter<boolean>) {
    return useQuery({
        queryKey: [...systemKey, "browser"],
        enabled,
        retry: false,
        queryFn: ({ signal }) => api.coreApiSystemBrowser({ signal }),
    });
}

/**
 * Keep the system queries current from the server's change notices instead of polling.
 *
 * The socket carries topics only (`{"type": "hello" | "changed", "topics": [...]}`); the data
 * still comes from the REST endpoints. The server throttles each topic, so a running forecast
 * refetches the cell layer every few seconds, not per cell. Every connection opens with a
 * `hello` naming all topics: ignored the first time (the queries have just loaded), acted on
 * after a reconnect, to catch up on what changed meanwhile. In a hidden tab the topics are
 * collected and refetched once it is visible again.
 */
export function useSystemEvents(enabled: MaybeRefOrGetter<boolean>) {
    const queryClient = useQueryClient();
    const visibility = useDocumentVisibility();
    const pending = new Set<string>();
    let greeted = false;
    let refused = false;

    const flush = () => {
        if (!pending.size || visibility.value !== "visible") return;
        const topics = [...pending];
        pending.clear();
        // cancelRefetch: false -- a slow page load in flight finishes rather than restarting.
        void queryClient.invalidateQueries(
            { predicate: query => systemQueryAffected(topics, query.queryKey) },
            { cancelRefetch: false },
        );
    };

    const socket = useWebSocket(`${useBackendHost(location.protocol === "https:" ? "wss:" : "ws:")}/ws/system/`, {
        immediate: false,
        autoReconnect: {
            retries: () => !refused && toValue(enabled),
            delay: retries => Math.min(30_000, 1000 * 2 ** retries),
        },
        onDisconnected: (_socket, event) => {
            if (event.code === REFUSED) refused = true;
        },
        onMessage: (_socket, event) => {
            if (typeof event.data !== "string") return;
            let message: unknown;
            try {
                message = JSON.parse(event.data);
            } catch {
                return;
            }
            if (typeof message !== "object" || message === null || !("topics" in message)) return;
            if (!Array.isArray(message.topics)) return;
            if ("type" in message && message.type === "hello" && !greeted) {
                greeted = true;
                return;
            }
            for (const topic of message.topics) pending.add(String(topic));
            flush();
        },
    });

    watch(visibility, flush);
    watch(
        () => toValue(enabled),
        on => {
            if (on) {
                refused = false;
                socket.open();
            } else {
                socket.close();
            }
        },
        { immediate: true },
    );

    return { live: computed(() => socket.status.value === "OPEN") };
}
