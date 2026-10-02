import { afterEach, describe, expect, it, vi } from "vitest";
import type { StyleSpecification } from "maplibre-gl";
import { isDarkMap, readStoredBasemap, satelliteStyle, SWISSIMAGE_BOUNDS } from "../basemap";

const labels: StyleSpecification = {
    version: 8,
    glyphs: "https://example.test/{fontstack}/{range}.pbf",
    sources: { carto: { type: "vector", url: "https://example.test/tiles.json" } },
    layers: [
        { id: "background", type: "background" },
        { id: "water", type: "fill", source: "carto", "source-layer": "water" },
        { id: "road", type: "line", source: "carto", "source-layer": "transportation" },
        { id: "place_town", type: "symbol", source: "carto", "source-layer": "place" },
        { id: "roadname_pri", type: "symbol", source: "carto", "source-layer": "transportation_name" },
    ],
};

describe("satellite basemap", () => {
    it("puts the imagery under the label layers and drops everything else", () => {
        const style = satelliteStyle(labels);
        expect(style.layers.map(layer => layer.id)).toEqual([
            "satellite-background", "satellite", "place_town", "roadname_pri",
        ]);
        expect(style.glyphs).toBe(labels.glyphs);
        expect(style.sources.carto).toBeDefined();
    });

    it("asks for SWISSIMAGE tiles inside Switzerland only", () => {
        const source = satelliteStyle(labels).sources.swissimage;
        expect(source?.type).toBe("raster");
        expect(source && "bounds" in source ? source.bounds : undefined).toEqual(SWISSIMAGE_BOUNDS);
    });

    it("counts imagery as a dark map for the route colours", () => {
        expect(isDarkMap("satellite", false)).toBe(true);
        expect(isDarkMap("map", false)).toBe(false);
        expect(isDarkMap("map", true)).toBe(true);
    });
});

describe("stored basemap", () => {
    afterEach(() => {
        vi.restoreAllMocks();
        localStorage.clear();
    });

    it("reads the stored choice", () => {
        localStorage.setItem("norain.basemap", "satellite");
        expect(readStoredBasemap()).toBe("satellite");
    });

    it("falls back to the map when storage is blocked or holds nonsense", () => {
        localStorage.setItem("norain.basemap", "terrain");
        expect(readStoredBasemap()).toBe("map");
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        expect(readStoredBasemap()).toBe("map");
    });
});
