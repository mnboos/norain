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

function mountChoice(avoidRain: boolean, avoidHeadwind: boolean) {
    return mount(WeatherRoutingChoice, {
        props: { avoidRain, avoidHeadwind },
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
        const [rain, wind] = toggles(wrapper);
        expect([rain?.props("modelValue"), wind?.props("modelValue")]).toEqual([false, true]);
        expect(toggles(wrapper).every(t => !t.props("disable"))).toBe(true);
        rain?.vm.$emit("update:modelValue", true);
        wind?.vm.$emit("update:modelValue", false);
        await wrapper.vm.$nextTick();
        expect(wrapper.emitted("update:avoidRain")).toEqual([[true]]);
        expect(wrapper.emitted("update:avoidHeadwind")).toEqual([[false]]);
        expect(wrapper.text()).not.toContain("Plus ansehen");
    });

    it("shows it locked and off without Plus", () => {
        const wrapper = mountChoice(true, true);
        expect(toggles(wrapper).map(t => t.props("modelValue") === true)).toEqual([false, false]);
        expect(toggles(wrapper).every(t => t.props("disable"))).toBe(true);
        expect(wrapper.text()).toContain("Plus ansehen");
    });
});
