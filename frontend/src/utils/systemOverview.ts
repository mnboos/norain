import type { FeatureCollection, Feature } from "geojson";
import type { SystemFeature, SystemCoveragePoint } from "@norain/api/models";

export const CACHE_COLORS = { fresh: "#15956a", aging: "#c78300", stale: "#dc4954" };
export const COVERAGE_COLORS = { usable: "#15956a", stale: "#dc4954", missing: "#7b8090", insufficient: "#a065d1" };
export const COVERAGE_LABELS = {
    usable: "Nutzbar",
    stale: "Veraltet",
    missing: "Fehlend",
    insufficient: "Nicht ausreichend",
};

export function cacheFreshness(fetchedAt: Date, now: number, maxAgeSeconds: number): keyof typeof CACHE_COLORS {
    const age = Math.max(0, (now - fetchedAt.getTime()) / 1000);
    return age > maxAgeSeconds ? "stale" : age >= maxAgeSeconds / 2 ? "aging" : "fresh";
}

export function featureKey(feature: SystemFeature): string {
    return `${feature.kind}:${feature.id}`;
}

export function systemGeoJson(items: SystemFeature[], now: number, maxAgeSeconds: number): FeatureCollection {
    return {
        type: "FeatureCollection",
        features: items.map((item): Feature => {
            const cell = item.kind === "forecast" || item.kind === "ensemble";
            return {
                type: "Feature",
                id: featureKey(item),
                geometry: cell
                    ? { type: "Polygon", coordinates: [item.coordinates] }
                    : { type: "LineString", coordinates: item.coordinates },
                properties: {
                    key: featureKey(item),
                    cell,
                    color:
                        cell && item.fetchedAt
                            ? CACHE_COLORS[cacheFreshness(item.fetchedAt, now, maxAgeSeconds)]
                            : item.kind === "stage"
                              ? "#9264cf"
                              : "#2186bd",
                    alternative: (item.rank ?? 0) > 0,
                    inactive: item.active === false,
                },
            };
        }),
    };
}

export function coverageGeoJson(points: SystemCoveragePoint[], kind: "forecast" | "ensemble"): FeatureCollection {
    return {
        type: "FeatureCollection",
        features: points.map(point => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [point.lon, point.lat] },
            properties: { color: COVERAGE_COLORS[point[kind]], status: point[kind] },
        })),
    };
}
