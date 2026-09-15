import { describe, expect, it } from "vitest";
import type { PlacesSearchResult } from "@norain/api/models";
import { placeLabel, placeSecondaryLine } from "../placeLabel";

function place(properties: Partial<PlacesSearchResult["properties"]>): PlacesSearchResult {
    return {
        type: "Feature",
        properties: { name: "Bahnhof", city: "Weinfelden", state: "Thurgau", countrycode: "CH", showCanton: false, ...properties },
        geometry: { type: "Point", coordinates: [9.1, 47.6] },
    };
}

describe("place labels", () => {
    it("names the place and its city on one line", () => {
        expect(placeLabel(place({}))).toBe("Bahnhof, Weinfelden");
        expect(placeSecondaryLine(place({}))).toBe("Weinfelden");
    });
    it("does not repeat a city that is the place itself", () => {
        expect(placeLabel(place({ name: "Weinfelden" }))).toBe("Weinfelden");
        expect(placeSecondaryLine(place({ name: "Weinfelden" }))).toBe("Thurgau");
    });
    it("adds the canton only when asked and known", () => {
        expect(placeLabel(place({ showCanton: true }))).toBe("Bahnhof, Weinfelden (TG)");
        expect(placeLabel(place({ showCanton: true, state: "Bavaria" }))).toBe("Bahnhof, Weinfelden");
    });
    it("copes with the shape built from a saved route", () => {
        expect(placeLabel(place({ city: null, state: "" }))).toBe("Bahnhof");
        expect(placeSecondaryLine(place({ city: null, state: "" }))).toBe("");
        expect(placeLabel(place({ name: "" }))).toBe("Unknown");
    });
});
