import { mount } from "@vue/test-utils";
import { Quasar, QToggle } from "quasar";
import { computed, ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WeatherRoutingChoice from "../WeatherRoutingChoice.vue";

const plus = ref(false);
vi.mock("@/composables/useEntitlements", () => ({
    useEntitlements: () => ({ weatherRouting: computed(() => plus.value) }),
}));

function toggles(wrapper: ReturnType<typeof mountChoice>) {
    return wrapper.findAllComponents(QToggle);
}

function mountChoice(avoidRain: boolean, avoidHeadwind: boolean, headwind = true, avoidShade = false) {
    return mount(WeatherRoutingChoice, {
        props: { avoidRain, avoidHeadwind, avoidShade, headwind },
        global: { plugins: [Quasar], stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
}

beforeEach(() => {
    plus.value = false;
});

describe("WeatherRoutingChoice", () => {
    it("lets a Plus rider switch each part on and off", async () => {
        plus.value = true;
        const wrapper = mountChoice(false, true);
        const [rain, wind, shade] = toggles(wrapper);
        expect([rain, wind, shade].map(t => t?.props("modelValue") === true)).toEqual([false, true, false]);
        expect(toggles(wrapper).every(t => !t.props("disable"))).toBe(true);
        rain?.vm.$emit("update:modelValue", true);
        wind?.vm.$emit("update:modelValue", false);
        shade?.vm.$emit("update:modelValue", true);
        await wrapper.vm.$nextTick();
        expect(wrapper.emitted("update:avoidRain")).toEqual([[true]]);
        expect(wrapper.emitted("update:avoidHeadwind")).toEqual([[false]]);
        expect(wrapper.emitted("update:avoidShade")).toEqual([[true]]);
        expect(wrapper.text()).not.toContain("Plus ansehen");
    });

    it("shows it locked and off without Plus", () => {
        const wrapper = mountChoice(true, true, true, true);
        expect(toggles(wrapper).map(t => t.props("modelValue") === true)).toEqual([false, false, false]);
        expect(toggles(wrapper).every(t => t.props("disable"))).toBe(true);
        expect(wrapper.text()).toContain("Plus ansehen");
    });

    it("offers no headwind switch for a hike, but shade", () => {
        plus.value = true;
        const wrapper = mountChoice(true, false, false, true);
        expect(toggles(wrapper).map(t => String(t.props("label")))).toEqual(["Regen ausweichen", "Schatten meiden"]);
        expect(toggles(wrapper).map(t => t.props("modelValue") === true)).toEqual([true, true]);
        expect(wrapper.text()).toContain("nicht im Schatten von Bergen oder Wald");
    });
});
