import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { Quasar } from "quasar";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import type { RecurringRouteOut } from "@norain/api/models";

import { recurringRouteKeys } from "@/queries/recurringRoutes";
import RouteDetailPage from "../[id].vue";

// q-tabs watches its own size; jsdom has no ResizeObserver.
vi.stubGlobal(
    "ResizeObserver",
    class {
        observe = vi.fn();
        unobserve = vi.fn();
        disconnect = vi.fn();
    },
);

const BASE = {
    description: "",
    startLat: 46.95,
    startLon: 7.44,
    startName: "Bern",
    destLat: 46.76,
    destLon: 7.63,
    destName: "Thun",
    profile: "bike",
    scheduleDescription: "",
    active: true,
    hasGeometry: true,
};

function routes(outboundNext: string, returnNext: string): RecurringRouteOut[] {
    return [
        {
            ...BASE,
            id: "out",
            name: "Hin",
            scheduleCron: "0 7 * * *",
            nextDeparture: outboundNext,
            returnRouteId: "back",
            returnNextDeparture: returnNext,
        },
        {
            ...BASE,
            id: "back",
            name: "Zurück",
            scheduleCron: "0 17 * * *",
            nextDeparture: returnNext,
            parentRouteId: "out",
        },
    ];
}

async function mountPage(path: string, seeded: RecurringRouteOut[]) {
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [
            { path: "/routes/:id", component: RouteDetailPage },
            { path: "/:p(.*)*", component: { template: "<div />" } },
        ],
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    for (const route of seeded) queryClient.setQueryData(recurringRouteKeys.detail(route.id), route);
    await router.push(path);
    await router.isReady();
    const wrapper = mount(
        { template: "<router-view />" },
        {
            global: {
                plugins: [Quasar, router, [VueQueryPlugin, { queryClient }]],
                stubs: {
                    QPage: { template: "<div><slot /></div>" },
                    RouteDetailPanel: {
                        props: ["route", "departureDate", "departureTime"],
                        template: "<div class='panel'>{{ route.name }} {{ departureTime }}</div>",
                    },
                },
            },
        },
    );
    await flushPromises();
    return { wrapper, router };
}

describe("route page direction", () => {
    it("opens the outbound ride while it is under way", async () => {
        // The outbound ride left an hour ago and is still being ridden; the return is later.
        const { wrapper, router } = await mountPage(
            "/routes/out",
            routes("2026-10-02T07:00:00+02:00", "2026-10-02T17:00:00+02:00"),
        );
        expect(wrapper.find(".panel").text()).toContain("Hin");
        expect(router.currentRoute.value.query.direction).toBeUndefined();
    });

    it("opens the return when it is next", async () => {
        const { wrapper } = await mountPage(
            "/routes/out",
            routes("2026-10-03T07:00:00+02:00", "2026-10-02T17:00:00+02:00"),
        );
        expect(wrapper.find(".panel").text()).toContain("Zurück");
    });

    it("keeps the tab chosen in the query", async () => {
        const { wrapper, router } = await mountPage(
            "/routes/out?direction=outbound",
            routes("2026-10-03T07:00:00+02:00", "2026-10-02T17:00:00+02:00"),
        );
        expect(wrapper.find(".panel").text()).toContain("Hin");

        await wrapper.findAll(".q-tab").at(1)?.trigger("click");
        await flushPromises();
        expect(router.currentRoute.value.query.direction).toBe("return");
        expect(wrapper.find(".panel").text()).toContain("Zurück");
    });

    it("opens a return route's link on the outbound page's return tab", async () => {
        const { wrapper, router } = await mountPage(
            "/routes/back",
            routes("2026-10-03T07:00:00+02:00", "2026-10-02T17:00:00+02:00"),
        );
        expect(router.currentRoute.value.path).toBe("/routes/out");
        expect(router.currentRoute.value.query.direction).toBe("return");
        expect(wrapper.find(".panel").text()).toContain("Zurück");
    });
});
