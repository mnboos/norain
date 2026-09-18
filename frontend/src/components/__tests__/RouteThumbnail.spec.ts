import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import RouteThumbnail, { type ThumbnailRoute } from "../RouteThumbnail.vue";
import { NO_DATA_COLOR, scoreColor } from "@/utils/rideQuality";

const DEPARTURE = "2026-09-14T08:00:00+02:00";

/** An L-shaped path so the glyph has a recognisable, non-degenerate shape. */
const PATH = [
    [9.0, 47.0],
    [9.01, 47.0],
    [9.02, 47.0],
    [9.02, 47.01],
    [9.02, 47.02],
];

/** A route whose thumbnail is served the way the API does: the worst sample already scored. */
function route(
    overrides: Partial<ThumbnailRoute> = {},
    rideScore: number | null = 0.1,
    rideLabel: string | null = "sehr gut",
    rainLevel: string | null = null,
): ThumbnailRoute {
    return {
        hasGeometry: true,
        nextDeparture: DEPARTURE,
        thumbnail: { departure: DEPARTURE, path: PATH, rideScore, rideLabel, rainLevel },
        ...overrides,
    };
}

/** The coloured line's strokes, without the casing underneath it. */
const strokes = (wrapper: ReturnType<typeof mount>) =>
    wrapper.findAll("polyline:not([data-casing])").map(p => p.attributes("stroke"));

describe("RouteThumbnail", () => {
    it("strokes the whole line using the server's ride score", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route({}, 0.3) } });
        expect(strokes(wrapper)).toEqual([scoreColor(0.3)]);
    });

    it("colours a dry ride, rather than greying it out as if there were no forecast", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route({}, 0.1, "sehr gut", null) } });
        expect(strokes(wrapper)).not.toEqual([NO_DATA_COLOR]);
    });

    it("distinguishes good from bad rides and keeps the overall quality label", () => {
        const good = mount(RouteThumbnail, { props: { route: route({}, 0.1, "sehr gut") } });
        const bad = mount(RouteThumbnail, { props: { route: route({}, 0.9, "sehr schlecht · v. a. Regen", "stark") } });
        expect(strokes(bad)).not.toEqual(strokes(good));
        // The colour has no legend at this size, so the label must keep naming the quality.
        expect(bad.attributes("aria-label")).toBe("Fahrqualität: sehr schlecht · v. a. Regen");
    });

    it("is grey when the server could not score any sample", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route({}, null, null) } });
        expect(strokes(wrapper)).toEqual([NO_DATA_COLOR]);
        expect(wrapper.attributes("aria-label")).toContain("Nicht verfügbar");
    });

    it("draws the casing under the coloured line", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route() } });
        const lines = wrapper.findAll("polyline");
        expect(lines).toHaveLength(2);
        expect(lines[0]?.attributes("data-casing")).toBeDefined();
        expect(lines[0]?.attributes("stroke")).toBe("currentColor");
        expect(lines[1]?.attributes("points")).toBe(lines[0]?.attributes("points"));
    });

    it("greys out a thumbnail computed for a departure that has already passed", () => {
        const wrapper = mount(RouteThumbnail, {
            props: { route: route({ nextDeparture: "2026-09-15T08:00:00+02:00" }, 0.9) },
        });
        expect(strokes(wrapper)).toEqual([NO_DATA_COLOR]);
        expect(wrapper.attributes("aria-label")).toContain("Noch keine Prognose");
    });

    it("greys out a route that has no upcoming departure at all", () => {
        const wrapper = mount(RouteThumbnail, { props: { route: route({ nextDeparture: null }) } });
        expect(strokes(wrapper)).toEqual([NO_DATA_COLOR]);
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
});
