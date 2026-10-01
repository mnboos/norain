import { describe, expect, it } from "vitest";
import {
    browserStatTotals,
    cacheFreshness,
    systemGeoJson,
    coverageGeoJson,
    dataCoverageGeoJson,
    ELEVATION_COLORS,
    statsUnder,
    systemQueryAffected,
    SYSTEM_TOPICS,
} from "../systemOverview";
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

describe("map data coverage", () => {
    const roads = [[8, 46, 9, 47]];
    const elevation = { full: [[8, 46, 8.5, 47]], partial: [], fallback: [[8.5, 46, 9, 47]] };

    it("draws only the layers switched on, as closed boxes", () => {
        expect(dataCoverageGeoJson(roads, elevation, { roads: false, elevation: false }).features).toEqual([]);
        const both = dataCoverageGeoJson(roads, elevation, { roads: true, elevation: true }).features;
        expect(both.map(feature => String(feature.properties?.layer))).toEqual(["elevation", "elevation", "roads"]);
        expect(both[1]?.properties?.color).toBe(ELEVATION_COLORS.fallback);
        const geometry = both[2]?.geometry;
        expect(geometry?.type === "Polygon" && geometry.coordinates[0]).toEqual([
            [8, 46],
            [9, 46],
            [9, 47],
            [8, 47],
            [8, 46],
        ]);
    });

    it("draws the roads before the elevation report has loaded", () => {
        const features = dataCoverageGeoJson(roads, undefined, { roads: true, elevation: true }).features;
        expect(features).toHaveLength(1);
    });

    it("is never refetched by a change notice", () => {
        expect(systemQueryAffected(SYSTEM_TOPICS, ["system", "dataCoverage"])).toBe(false);
    });
});

describe("system change notices", () => {
    it("refetch only the panels that read what changed", () => {
        const map = (layer: string) => ["system", "map", { layer, bbox: "7,46,9,48" }];
        expect(systemQueryAffected(["jobs"], ["system", "jobs", 0])).toBe(true);
        expect(systemQueryAffected(["jobs"], ["system", "summary"])).toBe(false);
        expect(systemQueryAffected(["jobs"], map("cells"))).toBe(false);
        expect(systemQueryAffected(["cells"], map("cells"))).toBe(true);
        expect(systemQueryAffected(["cells"], map("routes"))).toBe(false);
        expect(systemQueryAffected(["cells"], ["system", "history", 47, 8, 0])).toBe(true);
        expect(systemQueryAffected(["routes"], ["system", "history", 47, 8, 0])).toBe(false);
        expect(systemQueryAffected(["journeys"], ["system", "coverage", "stage", "x"])).toBe(true);
        expect(systemQueryAffected(["routes", "journeys"], map("journeys"))).toBe(true);
        expect(systemQueryAffected([...SYSTEM_TOPICS], ["routes", "list"])).toBe(false);
    });
});

describe("browser recognition stats", () => {
    it("sums the days and reads one group at a time", () => {
        const totals = browserStatTotals([
            { counts: { "tier:high": 3, "solo:realm_tampered": 1, "refused:pow": 2 } },
            { counts: { "tier:high": 1, "tier:low": 2 } },
        ]);
        expect(totals).toEqual({ "tier:high": 4, "tier:low": 2, "solo:realm_tampered": 1, "refused:pow": 2 });
        expect(statsUnder(totals, "tier:")).toEqual([
            ["high", 4],
            ["low", 2],
        ]);
        expect(statsUnder(totals, "ind:")).toEqual([]);
    });
});
