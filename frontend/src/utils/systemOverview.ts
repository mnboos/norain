import type { FeatureCollection, Feature } from "geojson";
import type { SystemFeature, SystemCoveragePoint, SystemElevationBoxes } from "@norain/api/models";

import { t } from "@/i18n";

export const CACHE_COLORS = { fresh: "#15956a", aging: "#c78300", stale: "#dc4954" };
export const COVERAGE_COLORS = { usable: "#15956a", stale: "#dc4954", missing: "#7b8090", insufficient: "#a065d1" };
/** The coverage states with their labels, in the current language. */
export function coverageLabels(): Record<keyof typeof COVERAGE_COLORS, string> {
    return {
        usable: t("system.coverage.usable"),
        stale: t("system.coverage.stale"),
        missing: t("system.coverage.missing"),
        insufficient: t("system.coverage.insufficient"),
    };
}

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

/** Zoom-15 terrain per road cell: complete, partly the zoom-12 fallback, or the fallback only. */
export const ELEVATION_COLORS = { full: "#1b9e9a", partial: "#e0a030", fallback: "#c2452d" };
const ELEVATION_LEVELS = ["full", "partial", "fallback"] as const;
export const ROAD_COVERAGE_COLOR = "#6b5fb5";
export function elevationLabels(): Record<keyof typeof ELEVATION_COLORS, string> {
    return {
        full: t("system.dataCoverage.elevation.full"),
        partial: t("system.dataCoverage.elevation.partial"),
        fallback: t("system.dataCoverage.elevation.fallback"),
    };
}

function boxFeature(box: number[], properties: Record<string, string>): Feature {
    const [west = 0, south = 0, east = 0, north = 0] = box;
    return {
        type: "Feature",
        geometry: {
            type: "Polygon",
            coordinates: [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ],
            ],
        },
        properties,
    };
}

/** The graph's road cells and the terrain levels (`/api/system/data-coverage`), as boxes per run of cells. */
export function dataCoverageGeoJson(
    roadBoxes: number[][],
    elevationBoxes: SystemElevationBoxes | undefined,
    show: { roads: boolean; elevation: boolean },
): FeatureCollection {
    const features: Feature[] = [];
    if (show.elevation && elevationBoxes) {
        for (const level of ELEVATION_LEVELS) {
            for (const box of elevationBoxes[level])
                features.push(boxFeature(box, { layer: "elevation", level, color: ELEVATION_COLORS[level] }));
        }
    }
    if (show.roads) {
        for (const box of roadBoxes) features.push(boxFeature(box, { layer: "roads", color: ROAD_COVERAGE_COLOR }));
    }
    return { type: "FeatureCollection", features };
}

/** What a change notice from `/ws/system/` names (`core/system_events.py`). */
export const SYSTEM_TOPICS = ["cells", "jobs", "routes", "journeys"] as const;

/** The panels, besides its own map layer, that read what each topic changes. */
const TOPIC_PANELS: Record<string, readonly string[]> = {
    cells: ["summary", "history", "coverage"],
    routes: ["summary", "coverage"],
    journeys: ["summary", "coverage"],
    jobs: ["jobs"],
};

/**
 * Whether a system query (`["system", panel, ...]`) reads data one of *topics* changed.
 * A map query is keyed by its params, and the layer in them is named like its topic.
 */
export function systemQueryAffected(topics: readonly string[], queryKey: readonly unknown[]): boolean {
    const [root, panel, params] = queryKey;
    if (root !== "system") return false;
    return topics.some(topic =>
        panel === "map"
            ? typeof params === "object" && params !== null && "layer" in params && params.layer === topic
            : (TOPIC_PANELS[topic] ?? []).includes(String(panel)),
    );
}

/** The daily recognition counts (`/api/system/browser/stats`) summed over every day returned. */
export function browserStatTotals(days: { counts: Record<string, number> }[]): Record<string, number> {
    const totals: Record<string, number> = {};
    for (const { counts } of days) {
        for (const [name, count] of Object.entries(counts)) totals[name] = (totals[name] ?? 0) + count;
    }
    return totals;
}

/** The totals under one prefix (`tier:`, `refused:`), prefix dropped, largest first. */
export function statsUnder(totals: Record<string, number>, prefix: string): [string, number][] {
    return Object.entries(totals)
        .filter(([name]) => name.startsWith(prefix))
        .map(([name, count]): [string, number] => [name.slice(prefix.length), count])
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}
