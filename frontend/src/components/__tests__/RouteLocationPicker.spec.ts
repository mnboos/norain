/* Named component stubs expose an untyped vm in Vue Test Utils. */
/* eslint-disable @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-member-access */
import type { ComponentPublicInstance } from "vue";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import RouteLocationPicker from "../RouteLocationPicker.vue";

vi.mock("../NiceMap.vue", () => ({ default: { name: "NiceMap", template: "<div />" } }));

function picker() {
    return mount(RouteLocationPicker, {
        global: {
            directives: { "close-popup": {} },
            stubs: {
                QBtn: { props: ["label", "disable"], template: '<button :disabled="disable">{{ label }}</button>' },
                QDialog: { name: "QDialog", props: ["modelValue"], template: '<div v-if="modelValue"><slot /></div>' },
                QCard: { template: "<div><slot /></div>" },
                QCardSection: { template: "<div><slot /></div>" },
                QCardActions: { template: "<div><slot /></div>" },
                QBtnToggle: true,
            },
        },
    });
}

describe("RouteLocationPicker", () => {
    it("stages map clicks and applies longitude/latitude to the correct endpoints", async () => {
        const wrapper = picker();
        await wrapper.get("button").trigger("click");
        const map = wrapper.findComponent<ComponentPublicInstance>({ name: "NiceMap" });
        map.vm.$emit("select-location", { lng: 9.2, lat: 47.5 });
        await wrapper.vm.$nextTick();
        expect(wrapper.text()).toContain("Zielpunkt durch Tippen");
        expect(wrapper.emitted("update:start")).toBeUndefined();
        map.vm.$emit("select-location", { lng: 9.3, lat: 47.6 });
        await wrapper.vm.$nextTick();
        await wrapper
            .findAll("button")
            .find(b => b.text() === "Übernehmen")
            ?.trigger("click");
        expect(wrapper.emitted("update:start")?.[0]?.[0]).toMatchObject({ geometry: { coordinates: [9.2, 47.5] } });
        expect(wrapper.emitted("update:dest")?.[0]?.[0]).toMatchObject({ geometry: { coordinates: [9.3, 47.6] } });
        wrapper.unmount();
    });
    it("discards draft points when the dialog is dismissed", async () => {
        const wrapper = picker();
        await wrapper.get("button").trigger("click");
        wrapper.findComponent<ComponentPublicInstance>({ name: "NiceMap" }).vm.$emit("select-location", {
            lng: 9.2,
            lat: 47.5,
        });
        wrapper.findComponent<ComponentPublicInstance>({ name: "QDialog" }).vm.$emit("update:modelValue", false);
        await wrapper.vm.$nextTick();
        await wrapper.get("button").trigger("click");
        expect(wrapper.text()).toContain("Start: Noch nicht gewählt");
        expect(wrapper.emitted("update:start")).toBeUndefined();
        wrapper.unmount();
    });
});
