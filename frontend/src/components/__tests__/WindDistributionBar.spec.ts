import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { Quasar } from "quasar";
import { type WindDistribution, WindDistributionTimingSourceEnum } from "@norain/api/models";
import WindDistributionBar from "../WindDistributionBar.vue";

const distribution: WindDistribution = { headwindM: 1234, crosswindM: 2000, tailwindM: 1000, calmM: 500, unknownM: 266,
    feltCoveredM: 4000, meanFeltSpeed: 22.25, timingSource: WindDistributionTimingSourceEnum.SampleInterpolation };

describe("wind distribution", () => {
    it("shows unknown and calm separately and exposes text independent of colour", () => {
        const wrapper = mount(WindDistributionBar, { props: { distribution }, global: { plugins: [Quasar] } });
        expect(wrapper.get('[role="img"]').attributes("aria-label")).toContain("0.3 km Unbekannt");
        expect(wrapper.text()).toContain("0.5 km Windstille");
        expect(wrapper.text()).toContain("verfügbar auf 4.0 km");
        expect(wrapper.text()).toContain("Fahrtempo näherungsweise");
        expect(wrapper.get('[role="img"] .q-linear-progress').attributes("style")).toContain("24.68%");
    });
    it("handles a zero length route without invalid widths", () => {
        const wrapper = mount(WindDistributionBar, { props: { distribution: { ...distribution,
            headwindM: 0, crosswindM: 0, tailwindM: 0, calmM: 0, unknownM: 0, feltCoveredM: 0 } } });
        expect(wrapper.text()).toContain("Keine Strecke");
        expect(wrapper.find('[role="img"]').exists()).toBe(false);
    });
});
