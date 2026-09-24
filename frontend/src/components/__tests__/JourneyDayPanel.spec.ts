import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import type { JourneyOut, JourneyDayOut } from "@norain/api/models";
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
    it("shows limits and unfulfilled POI wishes with only one alternative", () => {
        const reasons = [
            "Tageslimit: ~5 min zu lang",
            "Tageslimit: ~2.0 km zu weit",
            "Etappe 2: ~3 min zu lang",
            "Kein erreichbarer Stopp für Trinkwasser gefunden.",
        ];
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
        const day: JourneyDayOut = {
            id: "d",
            index: 0,
            date: new Date("2030-01-01"),
            start: [8, 47],
            end: [9, 47],
            forecastAvailable: false,
            stages: [
                { id: "s", rank: 0, path: [], distanceM: 2000, totalSeconds: 1000, breaks: [], gaps: {}, reasons },
            ],
        };
        const wrapper = mount(JourneyDayPanel, {
            props: {
                journey,
                day,
            },
            global: {
                stubs: Object.fromEntries(
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
            },
        });
        const warnings = wrapper.get('[data-testid="journey-limit-warnings"]');
        for (const reason of reasons) expect(warnings.text()).toContain(reason);
        wrapper.unmount();
    });
});
