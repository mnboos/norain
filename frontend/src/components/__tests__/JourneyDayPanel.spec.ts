import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import {
    JourneyReasonOutKindEnum as Kind,
    type JourneyOut,
    type JourneyDayOut,
    type JourneyReasonOut,
    type JourneyStageOut,
} from "@norain/api/models";
import JourneyDayPanel from "../journey/JourneyDayPanel.vue";

vi.mock("quasar", async importOriginal => ({
    ...(await importOriginal<typeof import("quasar")>()),
    useQuasar: () => ({ dark: { isActive: false } }),
}));
vi.mock("@/queries/journeys", () => ({
    useJourneyStageForecast: () => ({
        data: ref(),
        progress: ref(),
        isFetching: ref(false),
        error: ref(),
        isPending: ref(false),
    }),
    useJourneyStageForecasts: () => ref([]),
    useJourneyStagesPois: () => ref([]),
}));
vi.mock("@/components/NiceMap.vue", () => ({ default: { template: "<div />" } }));
vi.mock("@/components/ElevationChart.vue", () => ({ default: { template: "<div />" } }));
vi.mock("@/components/WeatherChart.vue", () => ({ default: { template: "<div />" } }));

describe("journey warnings", () => {
    it("keeps warnings visible and details follow variant selection, resetting on a new day", async () => {
        // The server sends reasons as data; the panel words them.
        const reasons: JourneyReasonOut[] = [
            { kind: Kind.DayLimit, minutes: 5 },
            { kind: Kind.DayLimit, km: 2 },
            { kind: Kind.LegLimit, leg: 2, minutes: 3 },
            { kind: Kind.MissingStop, category: "drinking_water" },
        ];
        const warningTexts = [
            "Tageslimit: ~5 min zu lang",
            "Tageslimit: ~2 km zu weit",
            "Etappe 2: ~3 min zu lang",
            "Kein erreichbarer Stopp für Trinkwasser gefunden.",
        ];
        const firstReason: JourneyReasonOut = { kind: Kind.Longer, percent: 12 };
        const secondReason: JourneyReasonOut = { kind: Kind.Longer, percent: 34 };
        const firstText = "12 % länger als die schnellste Variante";
        const secondText = "34 % länger als die schnellste Variante";
        const journey: JourneyOut = {
            id: "j",
            name: "Test journey",
            startLat: 47,
            startLon: 8,
            startName: "Start",
            destLat: 47,
            destLon: 9,
            destName: "Destination",
            viaPoints: [],
            profile: "bike",
            startDate: new Date("2030-01-01"),
            earliestStart: "08:00",
            latestArrival: "18:00",
            maxDaySeconds: null,
            maxDayDistanceM: null,
            maxLegSeconds: null,
            maxLegDistanceM: 1000,
            poiCategories: [],
            lodgingKinds: [],
            roadPrefs: {},
            weatherPrefs: {},
            planStatus: "planned",
        };
        const stage: JourneyStageOut = {
            id: "s",
            rank: 0,
            path: [],
            distanceM: 2000,
            totalSeconds: 1000,
            breaks: [],
            gaps: {},
            reasons,
        };
        const day: JourneyDayOut = {
            id: "d",
            index: 0,
            date: new Date("2030-01-01"),
            start: [8, 47],
            end: [9, 47],
            forecastAvailable: false,
            stages: [stage],
        };
        const wrapper = mount(JourneyDayPanel, {
            props: {
                journey,
                day,
            },
            global: {
                stubs: {
                    ...Object.fromEntries(
                        [
                            "QCard",
                            "QCardSection",
                            "QBanner",
                            "QSeparator",
                            "QIcon",
                            "QToggle",
                            "QBtn",
                            "QExpansionItem",
                        ].map(name => [name, { template: "<div><slot /></div>" }]),
                    ),
                    QExpansionItem: {
                        props: ["modelValue"],
                        emits: ["update:modelValue"],
                        template:
                            '<div><button data-testid="toggle-details" @click="$emit(\'update:modelValue\', !modelValue)">Details</button><div v-if="modelValue"><slot /></div></div>',
                    },
                },
            },
        });
        const variantButton = (index: number) => {
            const button = wrapper.findAll(".variant-button")[index];
            if (!button) throw new Error(`Variant button ${index} is missing`);
            return button;
        };
        const warnings = wrapper.get('[data-testid="journey-limit-warnings"]');
        for (const text of warningTexts) expect(warnings.text()).toContain(text);
        expect(wrapper.find(".variant-button").exists()).toBe(false);
        expect(wrapper.text()).not.toContain("Keine Pause nötig.");
        const first = { ...stage, reasons: [...reasons, firstReason], recommended: true };
        const second = { ...first, id: "s2", recommended: false, reasons: [secondReason], forecastStatus: "failed" };
        await wrapper.setProps({ day: { ...day, stages: [first, second] } });
        expect(wrapper.get(".variant-button").attributes("aria-pressed")).toBe("true");
        expect(wrapper.text()).toContain("Wetter fehlgeschlagen");
        await wrapper.get('[data-testid="toggle-details"]').trigger("click");
        expect(wrapper.text()).toContain(firstText);
        await variantButton(1).trigger("click");
        expect(wrapper.text()).toContain(secondText);
        expect(wrapper.text()).not.toContain(firstText);
        expect(variantButton(1).attributes("aria-pressed")).toBe("true");
        await wrapper.setProps({ day: { ...day, id: "next-day", stages: [first, second] } });
        expect(wrapper.text()).not.toContain(firstText);
        expect(wrapper.get(".variant-button").attributes("aria-pressed")).toBe("true");
        expect(wrapper.find('[data-testid="journey-charts"]').exists()).toBe(true);
        for (const text of warningTexts)
            expect(wrapper.get('[data-testid="journey-limit-warnings"]').text()).toContain(text);
        wrapper.unmount();
    });
});
