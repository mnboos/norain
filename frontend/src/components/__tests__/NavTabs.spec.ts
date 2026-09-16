import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { Quasar } from "quasar";
import { createMemoryHistory, createRouter } from "vue-router";
import NavTabs from "../NavTabs.vue";

// q-tabs watches its own size; jsdom has no ResizeObserver.
vi.stubGlobal("ResizeObserver", class { observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn(); });

function mountTabs(compact: boolean) {
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [
            { path: "/", component: { template: "<div />" } },
            { path: "/map", component: { template: "<div />" } },
        ],
    });
    return mount(NavTabs, { props: { compact }, global: { plugins: [Quasar, router] } });
}

describe("nav tabs", () => {
    it.each([false, true])("lists every page (compact: %s)", compact => {
        const labels = mountTabs(compact).findAll('[role="tab"]').map(tab => tab.text());
        expect(labels).toEqual(["Dashboard", "Karte"]);
    });
    it("shows the Dashboard icon only in the compact row", () => {
        expect(mountTabs(false).find('[role="tab"] .q-tab__icon').exists()).toBe(false);
        expect(mountTabs(true).find('[role="tab"] .q-tab__icon').exists()).toBe(true);
    });
});
