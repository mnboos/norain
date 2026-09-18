import { onScopeDispose, toValue, watch, type MaybeRefOrGetter } from "vue";
import { useQueryClient } from "@tanstack/vue-query";
import type { RecurringRouteOut } from "@norain/api/models";

import { useSession } from "@/composables/useSession";
import { prefetchForecastFigures } from "@/queries/forecastParts";
import { prefetchRecurringRouteForecast, recurringRouteKeys } from "@/queries/recurringRoutes";
import { pickPrefetchRoutes, recentRouteIds } from "@/utils/recentRoutes";

/**
 * Load the forecasts of the few routes the user is most likely to open next.
 *
 * Runs whenever the route list arrives or refreshes (every 60 s and on tab focus). A forecast
 * that is still fresh in the cache is not asked for again, so a repeat run costs nothing.
 *
 * One route at a time, on purpose: the backend plans and assembles forecasts on a single
 * worker, and loading several at once would put a click on another route at the back of
 * that queue. When the dashboard goes away (the user opened a route) no further prefetch
 * starts; the one already running is left to finish.
 */
export function usePrefetchRoutes(routes: MaybeRefOrGetter<RecurringRouteOut[] | undefined>) {
    const client = useQueryClient();
    const { session } = useSession();
    let running = false;
    // Aborted when the dashboard goes away.
    const lifetime = new AbortController();
    onScopeDispose(() => {
        lifetime.abort();
    });
    // A function, so the check is read again after each await.
    const stopped = () => lifetime.signal.aborted;

    async function prefetch(list: RecurringRouteOut[]) {
        const user = session.value.user;
        const userKey = user?.id ?? user?.username ?? "";
        for (const route of pickPrefetchRoutes(list, recentRouteIds(userKey))) {
            if (stopped()) return;
            const forecast = await prefetchRecurringRouteForecast(client, route);
            if (!forecast || stopped()) continue;
            // The charts load Plotly as its own chunk; fetch the code along with their data.
            void import("@/components/chart/NiceChart.vue").catch(() => undefined);
            await prefetchForecastFigures(client, forecast.jobId, forecast.version);
        }
    }

    watch(
        () => toValue(routes),
        list => {
            if (!list) return;
            // The list carries the same fields as the single-route endpoint, so the route page
            // can draw its header at once instead of waiting on its own request.
            // A detail answer newer than the list (say, from a save) is kept.
            const listUpdatedAt = client.getQueryState(recurringRouteKeys.lists())?.dataUpdatedAt ?? Date.now();
            for (const route of list) {
                const detailKey = recurringRouteKeys.detail(route.id);
                if ((client.getQueryState(detailKey)?.dataUpdatedAt ?? 0) > listUpdatedAt) continue;
                client.setQueryData(detailKey, route, { updatedAt: listUpdatedAt });
            }

            if (running || stopped()) return;
            running = true;
            void prefetch(list).finally(() => {
                running = false;
            });
        },
        { immediate: true },
    );
}
