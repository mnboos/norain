import { LocalStorage } from "quasar";
import type { RecurringRouteOut } from "@norain/api/models";

/** How many opened routes to remember per account. */
const MAX_RECENT = 10;

/** How many route forecasts the dashboard loads ahead. */
export const PREFETCH_LIMIT = 4;

/** The fields the choice reads. */
type PrefetchCandidate = Pick<RecurringRouteOut, "id" | "hasGeometry" | "forecastAvailable" | "nextDeparture">;

const storageKey = (userKey: string) => `norain.recentRoutes.${userKey}`;

/** The routes this browser opened for this account, newest first. */
export function recentRouteIds(userKey: string): string[] {
    try {
        const stored = LocalStorage.getItem(storageKey(userKey));
        return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [];
    } catch {
        return [];
    }
}

/** Remember that a route was opened. Losing this (private window, full storage) only makes the prefetch guess worse. */
export function recordRouteOpened(userKey: string, routeId: string): void {
    const ids = [routeId, ...recentRouteIds(userKey).filter(id => id !== routeId)].slice(0, MAX_RECENT);
    try {
        LocalStorage.set(storageKey(userKey), ids);
    } catch {
        /* Best effort. */
    }
}

/**
 * The routes worth loading before the user opens them.
 *
 * Takes turns between the routes that depart soonest (the list's own order) and the ones
 * opened most recently, so both a route about to be ridden and a route checked every day
 * are ready. Only routes whose forecast can be computed now count: without geometry the
 * endpoint refuses, and before the forecast window opens there is nothing to compute.
 */
export function pickPrefetchRoutes<T extends PrefetchCandidate>(
    routes: T[],
    recentIds: string[],
    limit = PREFETCH_LIMIT,
): T[] {
    const eligible = routes.filter(route => route.hasGeometry && route.forecastAvailable && !!route.nextDeparture);
    const byId = new Map(eligible.map(route => [route.id, route]));
    const recent = recentIds.flatMap(id => byId.get(id) ?? []);

    const picked = new Map<string, T>();
    for (let i = 0; picked.size < limit && (i < eligible.length || i < recent.length); i++) {
        for (const route of [eligible[i], recent[i]]) {
            if (route && picked.size < limit) picked.set(route.id, route);
        }
    }
    return [...picked.values()];
}
