import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Quasar, Screen } from "quasar";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import type { RecurringRouteOut } from "@norain/api/models";

import { recurringRouteKeys } from "@/queries/recurringRoutes";
import RouteListPanel from "../RouteListPanel.vue";

const ROUTE: RecurringRouteOut = {
    id: "r1",
    name: "Arbeitsweg",
    description: "",
    startLat: 46.95,
    startLon: 7.44,
    startName: "Bern",
    destLat: 46.76,
    destLon: 7.63,
    destName: "Thun",
    profile: "bike",
    scheduleCron: "0 7 * * 1-5",
    scheduleDescription: "Mo–Fr 07:00",
    active: true,
    hasGeometry: true,
};

const HINT = "Nach links wischen zum Löschen";

function mountPanel() {
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/:p(.*)*", component: { template: "<div />" } }],
    });
    // Each row reads its route from the list query; seeded, so nothing is fetched.
    const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    queryClient.setQueryData(recurringRouteKeys.lists(), [ROUTE]);
    return mount(RouteListPanel, {
        props: { routes: [ROUTE], loading: false },
        global: { plugins: [Quasar, router, [VueQueryPlugin, { queryClient }]] },
        attachTo: document.body,
    });
}

function setPhone(phone: boolean) {
    Screen.lt.sm = phone;
}

describe("RouteListPanel", () => {
    beforeEach(() => {
        localStorage.clear();
    });
    afterEach(() => {
        setPhone(false);
        document.body.innerHTML = "";
    });

    it("shows the delete button on wide screens only", () => {
        setPhone(false);
        expect(mountPanel().find('[aria-label="Route löschen"]').exists()).toBe(true);
        setPhone(true);
        expect(mountPanel().find('[aria-label="Route löschen"]').exists()).toBe(false);
    });

    it("hints at the gestures on phones until the hint is closed", async () => {
        setPhone(true);
        const wrapper = mountPanel();
        expect(wrapper.text()).toContain(HINT);
        await wrapper.find('[aria-label="Hinweis schliessen"]').trigger("click");
        expect(wrapper.text()).not.toContain(HINT);
        expect(mountPanel().text()).not.toContain(HINT);
    });

    it("never shows the hint on wide screens", () => {
        setPhone(false);
        expect(mountPanel().text()).not.toContain(HINT);
    });

    it("deletes from the context menu", async () => {
        const wrapper = mountPanel();
        await wrapper.find(".q-slide-item .q-item").trigger("contextmenu");
        await flushPromises();
        const items = [...document.querySelectorAll<HTMLElement>(".q-menu .q-item")];
        expect(items.map(i => i.textContent.trim())).toEqual(["Öffnen", "In neuem Tab öffnen", "Löschen"]);
        items[2]?.click();
        expect(wrapper.emitted("delete")).toEqual([["r1"]]);
    });
});
