import { afterEach, describe, expect, it, vi } from "vitest";
import { PlacesApi } from "@norain/api/apis";
import { useCurrentLocation } from "../useCurrentLocation";

function stubPosition(result: { coords?: { latitude: number; longitude: number }; code?: number }) {
    vi.stubGlobal("navigator", {
        geolocation: {
            getCurrentPosition: (ok: (position: unknown) => void, fail: (error: unknown) => void) => {
                if (result.coords) ok({ coords: result.coords });
                else fail({ code: result.code, PERMISSION_DENIED: 1, TIMEOUT: 3 });
            },
        },
    });
}

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe("useCurrentLocation", () => {
    it("names the position with the geocoder", async () => {
        stubPosition({ coords: { latitude: 47.5, longitude: 9.3 } });
        const place = {
            type: "Feature",
            properties: { name: "Hauptstrasse 3", city: "Amriswil", showCanton: false },
            geometry: { type: "Point", coordinates: [9.3, 47.5] },
        };
        const reverse = vi.spyOn(PlacesApi.prototype, "coreApiPlacesReverse").mockResolvedValue(place);
        const { locate, locating } = useCurrentLocation();
        await expect(locate()).resolves.toEqual(place);
        expect(reverse).toHaveBeenCalledWith({ lat: 47.5, lon: 9.3 });
        expect(locating.value).toBe(false);
    });

    it("falls back to the coordinates when the geocoder has no name", async () => {
        stubPosition({ coords: { latitude: 47.51234567, longitude: 9.3 } });
        vi.spyOn(PlacesApi.prototype, "coreApiPlacesReverse").mockRejectedValue(new Error("404"));
        const place = await useCurrentLocation().locate();
        expect(place.properties.name).toBe("47.51235, 9.30000");
        expect(place.geometry.coordinates).toEqual([9.3, 47.51234567]);
    });

    it("says when the user refused access", async () => {
        stubPosition({ code: 1 });
        await expect(useCurrentLocation().locate()).rejects.toThrow("nicht erlaubt");
    });
});
