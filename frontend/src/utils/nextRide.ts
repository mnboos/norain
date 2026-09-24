import type { RecurringRouteOut } from "@norain/api/models";

/** Return journeys have their own geometry, timing and forecast. */
export function nextRideId(
    route: Pick<RecurringRouteOut, "id" | "nextDeparture" | "returnRouteId" | "returnNextDeparture">,
): string {
    if (
        route.returnRouteId &&
        route.returnNextDeparture &&
        (!route.nextDeparture || Date.parse(route.returnNextDeparture) < Date.parse(route.nextDeparture))
    ) {
        return route.returnRouteId;
    }
    return route.id;
}
