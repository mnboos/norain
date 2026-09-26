import { describe, expect, it } from "vitest";
import { cacheFreshness, systemGeoJson, coverageGeoJson } from "../systemOverview";
import { SystemCoveragePointForecastEnum, SystemCoveragePointEnsembleEnum } from "@norain/api/models";

describe("system overview", () => {
    it("uses the cache expiry boundary and does not expire future timestamps", () => {
        const fetched = new Date("2030-01-01T00:00:00Z");
        expect(cacheFreshness(fetched, fetched.getTime() - 1, 7200)).toBe("fresh");
        expect(cacheFreshness(fetched, fetched.getTime() + 3600_000, 7200)).toBe("aging");
        expect(cacheFreshness(fetched, fetched.getTime() + 7200_000, 7200)).toBe("aging");
        expect(cacheFreshness(fetched, fetched.getTime() + 7200_001, 7200)).toBe("stale");
    });
    it("keeps route lines and cache outlines separate and marks inactive alternatives", () => {
        const output = systemGeoJson(
            [
                {
                    id: "route",
                    kind: "route",
                    coordinates: [
                        [8, 47],
                        [8.1, 47.1],
                    ],
                    active: false,
                },
                {
                    id: "stage",
                    kind: "stage",
                    coordinates: [
                        [8, 47],
                        [8.2, 47.1],
                    ],
                    rank: 1,
                },
                {
                    id: "cell",
                    kind: "forecast",
                    coordinates: [
                        [8, 47],
                        [8.01, 47],
                        [8, 47],
                    ],
                    fetchedAt: new Date(0),
                },
            ],
            8000_000,
            7200,
        );
        expect(output.features.map(feature => feature.geometry.type)).toEqual(["LineString", "LineString", "Polygon"]);
        expect(output.features[0]?.properties?.inactive).toBe(true);
        expect(output.features[1]?.properties?.alternative).toBe(true);
        expect(output.features[2]?.properties?.color).toBe("#dc4954");
    });
    it("switches coverage independently of cell freshness", () => {
        const points = [
            {
                lat: 47,
                lon: 8,
                forecast: SystemCoveragePointForecastEnum.Usable,
                ensemble: SystemCoveragePointEnsembleEnum.Missing,
            },
        ];
        expect(coverageGeoJson(points, "forecast").features[0]?.properties?.status).toBe("usable");
        expect(coverageGeoJson(points, "ensemble").features[0]?.properties?.status).toBe("missing");
    });
});
