import { QueryClient } from "@tanstack/vue-query";
import type { RecurringRouteOut } from "@norain/api/models";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { nextDepartureParts, prefetchRecurringRouteForecast, recurringRouteKeys } from "@/queries/recurringRoutes";
import { pickPrefetchRoutes, recentRouteIds, recordRouteOpened } from "@/utils/recentRoutes";

const store = new Map<string, unknown>();
vi.mock("quasar", () => ({
    LocalStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        set: (key: string, value: unknown) => store.set(key, value),
    },
}));

type TestRoute = Pick<
    RecurringRouteOut,
    | "id"
    | "hasGeometry"
    | "forecastAvailable"
    | "nextDeparture"
    | "departureFlexBeforeMinutes"
    | "departureFlexAfterMinutes"
>;

function route(id: string, overrides: Partial<TestRoute> = {}): TestRoute {
    return {
        id,
        hasGeometry: true,
        forecastAvailable: true,
        nextDeparture: "2026-09-18T07:30:00+02:00",
        departureFlexBeforeMinutes: 0,
        departureFlexAfterMinutes: 0,
        ...overrides,
    };
}

const ids = (routes: TestRoute[]) => routes.map(r => r.id);

describe("pickPrefetchRoutes", () => {
    it("takes turns between soonest departure and most recently opened", () => {
        const routes = ["a", "b", "c", "d", "e", "f"].map(id => route(id));
        expect(ids(pickPrefetchRoutes(routes, ["f", "e"], 4))).toEqual(["a", "f", "b", "e"]);
    });

    it("does not pick a route twice", () => {
        const routes = ["a", "b", "c"].map(id => route(id));
        expect(ids(pickPrefetchRoutes(routes, ["a", "b"], 4))).toEqual(["a", "b", "c"]);
    });

    it("stops at the limit", () => {
        const routes = ["a", "b", "c", "d", "e", "f"].map(id => route(id));
        expect(pickPrefetchRoutes(routes, [], 4)).toHaveLength(4);
    });

    it("skips routes whose forecast cannot be computed now, and recents no longer listed", () => {
        const routes = [
            route("no-geometry", { hasGeometry: false }),
            route("too-far", { forecastAvailable: false }),
            route("no-departure", { nextDeparture: null }),
            route("ok"),
        ];
        expect(ids(pickPrefetchRoutes(routes, ["deleted", "too-far"], 4))).toEqual(["ok"]);
    });
});

describe("recent routes", () => {
    beforeEach(() => {
        store.clear();
    });

    it("keeps the newest first, once each, per account, at most 10", () => {
        for (let i = 0; i < 12; i++) recordRouteOpened("u1", `r${i}`);
        recordRouteOpened("u1", "r5");
        const recent = recentRouteIds("u1");
        expect(recent[0]).toBe("r5");
        expect(recent).toHaveLength(10);
        expect(new Set(recent).size).toBe(10);
        expect(recentRouteIds("u2")).toEqual([]);
    });
});

describe("prefetchRecurringRouteForecast", () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("fills the cache entry the route page reads", async () => {
        const client = new QueryClient();
        const r = route("a", { departureFlexBeforeMinutes: 15, departureFlexAfterMinutes: 30 });
        const result = { jobId: "job", version: "1" };
        const query = vi.spyOn(client, "query").mockResolvedValue(result);

        expect(await prefetchRecurringRouteForecast(client, r)).toBe(result);

        // RouteDetailPanel: next departure, flex window from the route as numbers.
        const { date, time } = nextDepartureParts(r);
        expect(query.mock.calls[0]?.[0]).toMatchObject({
            queryKey: recurringRouteKeys.forecast("a", date, time, 15, 30),
        });
    });

    it("swallows a failed prefetch", async () => {
        const client = new QueryClient();
        vi.spyOn(client, "query").mockRejectedValue(new Error("409"));
        expect(await prefetchRecurringRouteForecast(client, route("a"))).toBeUndefined();
    });
});
