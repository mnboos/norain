import { flushPromises, mount } from "@vue/test-utils";
import type { ComponentPublicInstance } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ElevationOut } from "@norain/api/models";
import type { elevationFigure } from "@/utils/elevation";
import ElevationChart from "../ElevationChart.vue";

const api = vi.hoisted(() => ({ stage: vi.fn(), route: vi.fn(), preview: vi.fn() }));
vi.mock("@norain/api/apis", () => ({
    ElevationApi: class {
        coreApiElevationStageElevation = api.stage;
        coreApiElevationRouteElevation = api.route;
        coreApiElevationPreviewElevation = api.preview;
    },
}));

function profile(overrides: Partial<ElevationOut> = {}): ElevationOut {
    return {
        points: [
            { distanceM: 0, elapsedS: 0, elevationM: 100 },
            { distanceM: 1000, elapsedS: 120, elevationM: 200 },
        ],
        source: "Terrain",
        approximateTiming: false,
        ...overrides,
    };
}
function deferred() {
    let resolve!: (data: ElevationOut) => void;
    const promise = new Promise<ElevationOut>(r => {
        resolve = r;
    });
    return { promise, resolve };
}
const alternative = { stageId: "b", color: "#00ff00", label: "Variante 2" };
const cleanups: (() => void)[] = [];
function setup(props: InstanceType<typeof ElevationChart>["$props"] = { stageId: "a", alternatives: [alternative] }) {
    const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
    const wrapper = mount(ElevationChart, {
        props,
        global: {
            plugins: [[VueQueryPlugin, { queryClient: client }]],
            stubs: {
                NiceChart: {
                    name: "NiceChart",
                    props: { figure: Object, keepLineWidths: Boolean, xUnit: String },
                    template: "<div data-chart />",
                },
                QCard: { template: "<div><slot /></div>" },
                QBtn: { props: ["label"], template: "<button>{{ label }}</button>" },
                QSkeleton: { template: "<div data-loading />" },
                QBtnToggle: {
                    props: ["modelValue"],
                    emits: ["update:modelValue"],
                    template: `<button data-axis @click="$emit('update:modelValue', modelValue === 'distance' ? 'time' : 'distance')">Achse</button>`,
                },
            },
        },
    });
    cleanups.push(() => {
        wrapper.unmount();
        client.clear();
    });
    return {
        wrapper,
        client,
        chart: () => wrapper.findComponent({ name: "NiceChart" }),
        figure: () =>
            wrapper
                .getComponent<ComponentPublicInstance<{ figure: ReturnType<typeof elevationFigure> }>>({
                    name: "NiceChart",
                })
                .props("figure"),
        retry: () => {
            const button = wrapper.findAll("button").find(b => b.text() === "Erneut versuchen");
            if (!button) throw new Error("Retry button is missing");
            return button.trigger("click");
        },
    };
}
async function settle() {
    await flushPromises();
    await vi.advanceTimersByTimeAsync(1100);
    await flushPromises();
}

beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => {
    cleanups.splice(0).forEach(cleanup => {
        cleanup();
    });
    vi.useRealTimers();
});

describe("ElevationChart", () => {
    it("shows an alternative while the selected profile loads, then adds the primary", async () => {
        const main = deferred();
        api.stage.mockImplementation(({ stageId }: { stageId: string }) =>
            stageId === "a" ? main.promise : profile(),
        );
        const h = setup();
        await settle();
        expect(h.figure().data).toHaveLength(1);
        expect(h.wrapper.text()).toContain("gewählte Strecke wird geladen");
        expect(h.wrapper.find("[data-loading]").exists()).toBe(false);
        main.resolve(profile());
        await settle();
        expect(h.figure().data).toHaveLength(2);
        expect(h.wrapper.find('[role="status"]').exists()).toBe(false);
    });

    it("keeps alternatives visible through selected-profile failure and retry", async () => {
        api.stage.mockImplementation(({ stageId }: { stageId: string }) =>
            stageId === "a" ? Promise.reject(new Error("offline")) : profile(),
        );
        const h = setup();
        await settle();
        expect(h.figure().data).toHaveLength(1);
        expect(h.wrapper.get('[role="alert"]').text()).toContain("gewählte Strecke");
        const retried = deferred();
        api.stage.mockImplementation(() => retried.promise);
        await h.retry();
        await flushPromises();
        expect(h.figure().data).toHaveLength(1);
        retried.resolve(profile());
        await settle();
        expect(h.figure().data).toHaveLength(2);
        expect(h.wrapper.find('[role="alert"]').exists()).toBe(false);
    });

    it.each([{ points: [] }, { points: [{ distanceM: 0, elapsedS: 0, elevationM: null }] }])(
        "excludes an unusable selected profile and attributes the displayed alternative (%j)",
        async ({ points }) => {
            api.stage.mockImplementation(({ stageId }: { stageId: string }) =>
                stageId === "a"
                    ? profile({ points, source: "Hidden source" })
                    : profile({
                          source: "Alternative terrain",
                          approximateTiming: true,
                          points: [
                              { distanceM: 0, elapsedS: 0, elevationM: 10 },
                              { distanceM: 1000, elapsedS: 120, elevationM: null },
                          ],
                      }),
            );
            const h = setup();
            await settle();
            expect(h.figure().data).toHaveLength(1);
            expect(h.wrapper.text()).toContain("Keine Höhendaten für die gewählte Strecke");
            expect(h.wrapper.text()).toContain("Alternative terrain");
            expect(h.wrapper.text()).not.toContain("Hidden source");
            expect(h.wrapper.text()).toContain("teilweise nicht verfügbar");
            await h.wrapper.get("[data-axis]").trigger("click");
            expect(h.wrapper.text()).toContain("Fahrzeit nach Streckenlänge geschätzt");
            expect(h.figure().data[0]).toMatchObject({ x: [0, 2] });
        },
    );

    it("keeps a healthy selected profile when an alternative fails or has no heights", async () => {
        api.stage.mockImplementation(({ stageId }: { stageId: string }) => {
            if (stageId === "b") return Promise.reject(new Error("offline"));
            return stageId === "c" ? profile({ points: [] }) : profile();
        });
        const h = setup({ stageId: "a", alternatives: [alternative, { ...alternative, stageId: "c" }] });
        await settle();
        expect(h.figure().data).toHaveLength(1);
        expect(h.figure().data[0]).toMatchObject({ name: "Höhe" });
        expect(h.wrapper.find('[role="alert"]').exists()).toBe(false);
    });

    it("waits for remaining requests before showing an all-empty state", async () => {
        const other = deferred();
        api.stage.mockImplementation(({ stageId }: { stageId: string }) =>
            stageId === "a" ? profile({ points: [] }) : other.promise,
        );
        const h = setup();
        await settle();
        expect(h.chart().exists()).toBe(false);
        expect(h.wrapper.find("[data-loading]").exists()).toBe(true);
        other.resolve(profile({ points: [{ distanceM: 0, elapsedS: 0, elevationM: null }] }));
        await settle();
        expect(h.wrapper.find("[data-loading]").exists()).toBe(false);
        expect(h.wrapper.text()).toContain("Keine Höhendaten für diese Strecke verfügbar");
    });

    it("retries all failed requests when no profile is available", async () => {
        api.stage.mockRejectedValue(new Error("offline"));
        const h = setup();
        expect(h.wrapper.find("[data-loading]").exists()).toBe(true);
        await settle();
        expect(h.chart().exists()).toBe(false);
        expect(h.wrapper.find('[role="alert"]').exists()).toBe(true);
        api.stage.mockClear().mockResolvedValue(profile());
        await h.retry();
        await settle();
        expect(api.stage).toHaveBeenCalledTimes(2);
        expect(h.figure().data).toHaveLength(2);
    });

    it("retains cached usable profiles after refetch failures", async () => {
        api.stage.mockResolvedValue(profile());
        const h = setup();
        await settle();
        api.stage.mockRejectedValue(new Error("offline"));
        void h.client.invalidateQueries({ queryKey: ["elevation"] });
        await settle();
        expect(h.figure().data).toHaveLength(2);
        expect(h.wrapper.find('[role="alert"]').exists()).toBe(true);
    });

    it("switches cached variants without requests and updates labels, colours and emphasis", async () => {
        api.stage.mockResolvedValue(profile());
        const h = setup();
        await settle();
        expect(api.stage).toHaveBeenCalledTimes(2);
        await h.wrapper.setProps({
            stageId: "b",
            color: "#ffffff",
            label: "Variante 2",
            alternatives: [{ stageId: "a", color: "#ff0000", label: "Variante 1" }],
        });
        await flushPromises();
        expect(api.stage).toHaveBeenCalledTimes(2);
        expect(h.figure().data).toMatchObject([
            { name: "Variante 1", line: { color: "#ff0000", width: 2 } },
            { name: "Variante 2", line: { color: "#ffffff", width: 3.5 } },
        ]);
        expect(h.chart().props("keepLineWidths")).toBe(true);
    });

    it("drops previous-day profiles and ignores their late responses", async () => {
        const old = deferred();
        const next = deferred();
        api.stage.mockImplementation(({ stageId }: { stageId: string }) => {
            if (stageId === "b") return old.promise;
            if (stageId === "c") return next.promise;
            return profile();
        });
        const h = setup();
        await settle();
        expect(h.figure().data).toHaveLength(1);
        await h.wrapper.setProps({ stageId: "c", label: "Next day", alternatives: [] });
        await flushPromises();
        expect(h.chart().exists()).toBe(false);
        old.resolve(profile());
        await settle();
        expect(h.chart().exists()).toBe(false);
        next.resolve(profile({ points: [{ distanceM: 0, elapsedS: 0, elevationM: 900 }] }));
        await settle();
        expect(h.figure().data).toMatchObject([{ name: "Next day", y: [900] }]);
    });

    it.each(["route", "preview"] as const)("preserves single %s loading, error, retry and empty states", async kind => {
        api[kind].mockRejectedValue(new Error("offline"));
        const h = setup(
            kind === "route"
                ? { routeId: "route" }
                : {
                      coordinates: [
                          [9, 47],
                          [9.1, 47.1],
                      ],
                      totalSeconds: 120,
                  },
        );
        expect(h.wrapper.find("[data-loading]").exists()).toBe(true);
        await settle();
        expect(h.wrapper.find('[role="alert"]').exists()).toBe(true);
        api[kind].mockResolvedValue(profile({ points: [] }));
        await h.retry();
        await settle();
        expect(h.wrapper.text()).toContain("Keine Höhendaten für diese Strecke verfügbar");
        api[kind].mockResolvedValue(profile());
        void h.client.invalidateQueries({ queryKey: ["elevation"] });
        await settle();
        expect(h.figure().data).toMatchObject([{ name: "Höhe", line: { color: "#32966b" } }]);
    });
});
