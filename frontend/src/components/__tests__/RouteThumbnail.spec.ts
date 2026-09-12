import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import RouteThumbnail, { type ThumbnailRoute } from "../RouteThumbnail.vue";
import { NO_DATA_COLOR } from "@/utils/rideQuality";

const DEPARTURE = "2026-09-14T08:00:00+02:00";

/** An L-shaped path so the glyph has a recognisable, non-degenerate shape. */
const PATH = [
    [9.0, 47.0],
    [9.01, 47.0],
    [9.02, 47.0],
    [9.02, 47.01],
    [9.02, 47.02],
];

function sample(i: number, rainRateMmH: number, temp = 16, headwind = 4) {
    return { i, rainMm: rainRateMmH, precipitationIntervalS: 3600, rainRateMmH, temp, headwind };
}

function route(overrides: Partial<ThumbnailRoute> = {}): ThumbnailRoute {
    return {
        hasGeometry: true,
        nextDeparture: DEPARTURE,
        thumbnail: { departure: DEPARTURE, path: PATH, samples: [sample(0, 0), sample(2, 0), sample(4, 0)] },
        ...overrides,
    };
}

const strokes = (wrapper: ReturnType<typeof mount>) => wrapper.findAll("polyline").map(p => p.attributes("stroke"));

describe("RouteThumbnail", () => {
    it("draws one span per sample gap, in the theme ink", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route() } });
        const colors = strokes(wrapper);
        expect(colors).toHaveLength(2);
        // Dry, mild, light wind — a known forecast, so nothing is the no-data grey.
        for (const c of colors) expect(c).toBe("currentColor");
    });

    it("paints a span grey when either endpoint has no data", () => {
        const wrapper = mount(RouteThumbnail, {
            props: {
                route: route({
                    thumbnail: { departure: DEPARTURE, path: PATH, samples: [sample(0, 0), null, sample(4, 0)] },
                }),
            },
        });
        expect(strokes(wrapper)).toEqual([NO_DATA_COLOR, NO_DATA_COLOR]);
    });

    it("says a wet ride in text, not in colour", () => {
        const dry = mount(RouteThumbnail, {
            props: {
                route: route({
                    thumbnail: { departure: DEPARTURE, path: PATH, samples: [sample(0, 0), sample(4, 0)] },
                }),
            },
        });
        const wet = mount(RouteThumbnail, {
            props: {
                route: route({
                    thumbnail: { departure: DEPARTURE, path: PATH, samples: [sample(0, 0), sample(4, 5)] },
                }),
            },
        });
        // The glyph is shape only - a soaking and a dry ride stroke identically...
        expect(strokes(wet)).toEqual(strokes(dry));
        // ...so the label is the only channel that separates them, and it must.
        expect(wet.attributes("aria-label")).toContain("Regen");
        expect(wet.attributes("aria-label")).not.toBe(dry.attributes("aria-label"));
    });

    it("never varies the stroke across a wide quality range", () => {
        // Guards against reintroducing a per-span ramp: perfect at one end, awful at the
        // other, and every known span still strokes the same ink.
        const wrapper = mount(RouteThumbnail, {
            props: {
                route: route({
                    thumbnail: {
                        departure: DEPARTURE,
                        path: PATH,
                        samples: [sample(0, 0), sample(2, 0), sample(4, 9)],
                    },
                }),
            },
        });
        expect(new Set(strokes(wrapper))).toEqual(new Set(["currentColor"]));
    });

    it("greys out a thumbnail computed for a departure that has already passed", () => {
        const wrapper = mount(RouteThumbnail, {
            props: { route: route({ nextDeparture: "2026-09-15T08:00:00+02:00" }) },
        });
        expect(strokes(wrapper).every(c => c === NO_DATA_COLOR)).toBe(true);
        expect(wrapper.attributes("aria-label")).toContain("Noch keine Prognose");
    });

    it("greys out a route that has no upcoming departure at all", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route({ nextDeparture: null }) } });
        expect(strokes(wrapper).every(c => c === NO_DATA_COLOR)).toBe(true);
    });

    it("never relies on colour alone — the label repeats the quality as text", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route() } });
        expect(wrapper.attributes("role")).toBe("img");
        expect(wrapper.attributes("aria-label")).toMatch(/^Fahrqualität: /);
        expect(wrapper.find("title").text()).toBe(wrapper.attributes("aria-label"));
    });

    it("shows a placeholder instead of a broken box when geometry is missing", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route({ hasGeometry: false, thumbnail: null }) } });
        expect(wrapper.findAll("polyline")).toHaveLength(0);
        expect(wrapper.find("circle").exists()).toBe(true);
        expect(wrapper.attributes("aria-label")).toContain("berechnet");
    });

    it("still draws the shape when no sample has data", () => {
        const wrapper = mount(RouteThumbnail, {
            props: { route: route({ thumbnail: { departure: DEPARTURE, path: PATH, samples: [null, null] } }) },
        });
        expect(strokes(wrapper)).toEqual([NO_DATA_COLOR]);
    });
});
