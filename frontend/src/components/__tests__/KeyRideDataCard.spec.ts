import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ElevationOut, RouteForecastOut } from "@norain/api/models";
import { i18n } from "@/i18n";
import KeyRideDataCard from "../KeyRideDataCard.vue";

const api = vi.hoisted(() => ({ elevation: vi.fn() }));
vi.mock("@norain/api/apis", () => ({
    ElevationApi: class {
        coreApiElevationForecastElevation = api.elevation;
    },
}));

const forecast: RouteForecastOut = {
    jobId: "job-1",
    version: "v1",
    departureTime: "2026-10-03T08:00:00Z",
    line: [],
    totalSeconds: 600,
    totalDistanceM: 1000,
    samples: [],
    summary: { willRain: false, maxRainMm: 0, rainAmount: 0, source: "open-meteo" },
};

function profile(heights: (number | null)[]): ElevationOut {
    return {
        points: heights.map((elevationM, i) => ({ elevationM, distanceM: i * 100, elapsedS: i * 60 })),
        source: "Terrain",
    };
}

const cleanups: (() => void)[] = [];
function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const wrapper = mount(KeyRideDataCard, {
        props: { forecast },
        global: {
            plugins: [[VueQueryPlugin, { queryClient: client }]],
            stubs: {
                QCard: { template: "<div><slot /></div>" },
                QCardSection: { template: "<div><slot /></div>" },
                QItem: { template: '<div class="q-item"><slot /></div>' },
                QItemLabel: { template: "<div><slot /></div>" },
                QIcon: true,
                QSeparator: true,
                QDialog: true,
                QCardActions: true,
                QBtn: true,
            },
        },
    });
    cleanups.push(() => {
        wrapper.unmount();
        client.clear();
    });
    return wrapper;
}

beforeEach(() => {
    api.elevation.mockReset();
});
afterEach(() => {
    cleanups.splice(0).forEach(cleanup => {
        cleanup();
    });
    i18n.global.locale.value = "de";
});

describe("KeyRideDataCard elevation", () => {
    it("shows cumulative ascent and descent, and translates the tile", async () => {
        // Endpoints differ by only 40 m, but the ride climbs 120 m and descends 80 m.
        api.elevation.mockResolvedValue(profile([100, 170, 120, 170, 140]));
        const wrapper = setup();
        await flushPromises();
        expect(wrapper.text()).toContain("↑ 120 m · ↓ 80 m");
        expect(api.elevation.mock.calls[0]?.[0]).toEqual({ jobId: "job-1" });
        i18n.global.locale.value = "en";
        await flushPromises();
        expect(wrapper.text()).toContain("↑ 120 m · ↓ 80 m");
    });

    it.each([{ heights: [] }, { heights: [100] }, { heights: [100, null, 200] }])(
        "does not claim totals for incomplete data (%j)",
        async ({ heights }) => {
            api.elevation.mockResolvedValue(profile(heights));
            const wrapper = setup();
            await flushPromises();
            const tile = wrapper.findAll(".q-item").find(item => item.text().includes("Höhenmeter"));
            expect(tile?.text()).toContain("Nicht verfügbar");
        },
    );

    it("shows zero for a flat route", async () => {
        api.elevation.mockResolvedValue(profile([100, 100, 100]));
        const wrapper = setup();
        await flushPromises();
        expect(wrapper.text()).toContain("↑ 0 m · ↓ 0 m");
    });

    it("clears old totals while a new version of the same forecast loads", async () => {
        api.elevation.mockResolvedValueOnce(profile([100, 220, 140]));
        const wrapper = setup();
        await flushPromises();
        let resolve!: (value: ElevationOut) => void;
        api.elevation.mockImplementationOnce(
            () =>
                new Promise<ElevationOut>(r => {
                    resolve = r;
                }),
        );
        await wrapper.setProps({ forecast: { ...forecast, version: "v2" } });
        expect(wrapper.text()).not.toContain("↑ 120 m · ↓ 80 m");
        await flushPromises();
        resolve(profile([200, 250, 150]));
        await flushPromises();
        expect(wrapper.text()).toContain("↑ 50 m · ↓ 100 m");
        expect(api.elevation).toHaveBeenCalledTimes(2);
    });
});
