import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import RouteWeatherBadges from "../RouteWeatherBadges.vue";
import type { ThumbnailRoute } from "@/utils/routeThumbnail";

const DEPARTURE = "2026-09-14T08:00:00+02:00";

function route(
    thumbnail: NonNullable<ThumbnailRoute["thumbnail"]> | null,
    overrides: Partial<ThumbnailRoute> = {},
): ThumbnailRoute {
    return {
        hasGeometry: true,
        nextDeparture: DEPARTURE,
        thumbnail: thumbnail === null ? null : { departure: DEPARTURE, path: [], ...thumbnail },
        ...overrides,
    };
}

/** The two readings as the component renders them, in order. */
function readings(r: ThumbnailRoute) {
    const wrapper = mount(RouteWeatherBadges, { props: { route: r }, global: { stubs: { QIcon: true } } });
    return wrapper.findAll("span[title]").map(el => ({
        title: el.attributes("title"),
        text: el.text(),
        classes: el.classes(),
    }));
}

describe("RouteWeatherBadges", () => {
    it("shows the chance of rain and the coldest point of the ride", () => {
        const [rain, frost] = readings(
            route({ rainProbability: 0.6, maxRainRateMmH: 1.2, rainLevel: "mässig", tempMin: -2, frostLevel: "stark" }),
        );
        expect(rain?.text).toBe("60 %");
        // A minus sign, not a hyphen.
        expect(frost?.text).toContain("\u22122");
        expect(frost?.text).toContain("°C");
    });

    it("falls back to the amount when there is no probability (OpenWeatherMap path)", () => {
        const [rain] = readings(route({ rainProbability: null, maxRainRateMmH: 1.2, rainLevel: "leicht" }));
        expect(rain?.text).toBe("1.2 mm/h");
    });

    it("says the level in words, so the colour is never the only cue", () => {
        const [rain, frost] = readings(
            route({ rainProbability: 0.6, rainLevel: "mässig", tempMin: -2, frostLevel: "stark" }),
        );
        expect(rain?.title).toBe("Regenrisiko 60 %, mässig");
        expect(frost?.title).toContain("stark");
    });

    it("shows only the reading that fires", () => {
        // The case this component exists for: a mild wet day is rain, not frost.
        const wet = readings(route({ rainProbability: 0.6, rainLevel: "mässig", tempMin: 19, frostLevel: null }));
        expect(wet).toHaveLength(1);
        expect(wet[0]?.title).toContain("Regenrisiko");

        const icy = readings(route({ rainProbability: 0, rainLevel: null, tempMin: -4, frostLevel: "stark" }));
        expect(icy).toHaveLength(1);
        expect(icy[0]?.title).toContain("Frost");
    });

    it("says nothing when there is nothing to report", () => {
        // A calm, mild ride: the numbers exist, but an icon that is always there means
        // nothing - and a snowflake beside 19 °C means something wrong.
        expect(readings(route({ rainProbability: 0, rainLevel: null, tempMin: 19, frostLevel: null }))).toEqual([]);
        // Nothing to read at all: never a confident "kein Frost" - it claims nothing instead.
        expect(readings(route(null))).toEqual([]);
    });

    it("treats a thumbnail computed for a departure that has passed as no forecast", () => {
        const stale = route(
            { departure: "2026-09-13T08:00:00+02:00", rainProbability: 0.9, rainLevel: "stark", tempMin: -5,
              frostLevel: "stark" },
            { nextDeparture: DEPARTURE },
        );
        expect(readings(stale)).toEqual([]);
    });
});
