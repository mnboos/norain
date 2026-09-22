import { defineComponent, h, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GeometrySource, type RoutePlanIn } from "@norain/api/models";
import type { PlacesSearchResult } from "@norain/api/models";
import { useRouteWeather } from "@/queries/routeWeather";
import { useRecurringRouteForecast } from "@/queries/recurringRoutes";

const api = vi.hoisted(() => ({ adhoc: vi.fn(), recurring: vi.fn(), plan: vi.fn() }));
vi.mock("@norain/api/apis", () => ({
    GPXApi: class { coreApiGpxForecastRoutePlan = api.plan; },
    RouteWeatherApi: class {
        coreApiRouteWeatherRouteWeather = api.adhoc;
    },
    RecurringRoutesApi: class {
        coreApiRecurringRouteRouteForecast = api.recurring;
    },
}));
vi.mock("@/services/forecastJob", () => ({ awaitForecastJob: (job: { result: unknown }) => Promise.resolve(job.result) }));

const place: PlacesSearchResult = {
    type: "Feature",
    geometry: { type: "Point", coordinates: [9, 47] },
    properties: { name: "Start", city: null, state: null, countrycode: "CH", showCanton: false },
};

function setup<T>(hook: () => T) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    let query: T | undefined;
    const wrapper = mount(
        defineComponent({
            setup() {
                query = hook();
                return () => h("div");
            },
        }),
        {
            global: { plugins: [[VueQueryPlugin, { queryClient: client }]] },
        },
    );
    return {
        get query() {
            return query;
        },
        cleanup() {
            wrapper.unmount();
            client.clear();
        },
    };
}

describe("departure-window queries", () => {
    beforeEach(() => vi.clearAllMocks());

    it("isolates flexibility in the cache and ignores a late response for the old window", async () => {
        let resolveOld: (value: unknown) => void = () => undefined;
        api.adhoc.mockImplementationOnce(
            () =>
                new Promise(resolve => {
                    resolveOld = resolve;
                }),
        );
        api.adhoc.mockResolvedValue({ result: { departureTime: "new-window" } });
        const after = ref(30);
        const harness = setup(() => useRouteWeather(place, place, "bike", "2030-06-01T08:00", 0, after));
        await flushPromises();
        expect(api.adhoc).toHaveBeenNthCalledWith(1, expect.objectContaining({ departureFlexAfterMinutes: 30 }));
        after.value = 60;
        await flushPromises();
        expect(api.adhoc).toHaveBeenCalledTimes(2);
        expect(harness.query?.data.value?.departureTime).toBe("new-window");
        resolveOld({ result: { departureTime: "old-window" } });
        await flushPromises();
        expect(harness.query?.data.value?.departureTime).toBe("new-window");
        harness.cleanup();
    });

    it("keeps imported shape and timing in the forecast request and cache", async () => {
        api.plan.mockResolvedValue({ result: { departureTime: "imported" } });
        const plan = ref<RoutePlanIn>({ geometrySource: GeometrySource.Imported, coordinates: [[9, 47], [9.2, 47.2]], durationSeconds: 900 });
        const harness = setup(() => useRouteWeather(place, place, "bike", "2030-06-01T08:00", 0, 0, true, plan));
        await flushPromises();
        expect(api.plan.mock.calls[0]?.[0]).toMatchObject({ routePlanForecastIn: { coordinates: plan.value.coordinates, durationSeconds: 900 } });
        expect(api.adhoc).not.toHaveBeenCalled();
        plan.value = { ...plan.value, durationSeconds: 1800 };
        await flushPromises();
        expect(api.plan).toHaveBeenCalledTimes(2);
        harness.cleanup();
    });

    it("preserves the offset and sends explicit zero overrides for a selected saved-route departure", async () => {
        api.recurring.mockResolvedValue({ result: { departureTime: "selected" } });
        const enabled = ref(false);
        const harness = setup(() =>
            useRecurringRouteForecast("route-id", "2030-10-27", "02:15:00+01:00", enabled, 0, 0),
        );
        await flushPromises();
        expect(api.recurring).not.toHaveBeenCalled();
        enabled.value = true;
        await flushPromises();
        expect(api.recurring).toHaveBeenCalledWith({
            routeId: "route-id",
            date: "2030-10-27",
            time: "02:15:00+01:00",
            departureFlexBeforeMinutes: 0,
            departureFlexAfterMinutes: 0,
        });
        harness.cleanup();
    });
});
