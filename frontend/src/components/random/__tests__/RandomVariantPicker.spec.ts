import { flushPromises, mount } from "@vue/test-utils";
import { Dialog, Notify, Quasar, QCheckbox } from "quasar";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { JourneyOutFromJSON } from "@norain/api/models";
import RandomVariantPicker from "../RandomVariantPicker.vue";

interface SaveInput {
    stageIds: string[];
    scheduleCron: string;
    scheduleDescription: string;
    name: (index: number) => string;
}
const state = vi.hoisted(() => ({
    save: vi.fn<(input: SaveInput) => Promise<{ id: string }[]>>(),
    push: vi.fn<(to: string) => Promise<void>>(),
}));
vi.mock("@/queries/journeys", () => ({
    useSaveVariantsAsRoutes: () => ({ mutateAsync: state.save, isPending: { value: false } }),
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: state.push }) }));

function stage(id: string, distanceM: number) {
    return {
        id,
        rank: 0,
        distance_m: distanceM,
        total_seconds: 3600,
        path: [
            [8, 47],
            [8.1, 47],
        ],
        breaks: [],
    };
}

// Through the generated parser, as the page gets it; only what the picker reads is filled in.
const ride = JourneyOutFromJSON({
    id: "ride-1",
    name: "Runde",
    start_date: "2026-10-03", // a Saturday
    earliest_start: "09:30:00",
    days: [
        {
            id: "day-1",
            index: 0,
            date: "2026-10-03",
            start: [8, 47],
            end: [8, 47],
            stages: [stage("a", 20000), stage("b", 21000), stage("c", 22000)],
        },
    ],
});

function mountPicker() {
    return mount(RandomVariantPicker, {
        props: { ride },
        global: { plugins: [[Quasar, { plugins: { Notify, Dialog } }]], stubs: { VariantsMap: true } },
    });
}

beforeEach(() => {
    state.save.mockReset();
    state.push.mockReset();
});

describe("RandomVariantPicker", () => {
    it("saves the picked variants with the ride's day and time as schedule", async () => {
        state.save.mockResolvedValue([{ id: "r1" }, { id: "r2" }]);
        const wrapper = mountPicker();
        const boxes = wrapper.findAllComponents(QCheckbox);
        expect(boxes).toHaveLength(3);
        boxes[0]?.vm.$emit("update:modelValue", true);
        boxes[2]?.vm.$emit("update:modelValue", true);
        await flushPromises();
        await wrapper.get("[data-testid=save-variants]").trigger("click");
        await flushPromises();

        expect(state.save).toHaveBeenCalledOnce();
        const input = state.save.mock.calls[0]?.[0];
        expect(input?.stageIds).toEqual(["a", "c"]);
        expect(input?.scheduleCron).toBe("30 9 * * 6");
        expect(input?.scheduleDescription).toBe("Sa um 09:30");
        expect([input?.name(0), input?.name(1)]).toEqual(["Runde – Variante 1", "Runde – Variante 3"]);
        expect(state.push).toHaveBeenCalledWith("/");
    });

    it("cannot save before a variant is picked", () => {
        const wrapper = mountPicker();
        expect(wrapper.get("[data-testid=save-variants]").attributes("disabled")).toBeDefined();
    });
});
