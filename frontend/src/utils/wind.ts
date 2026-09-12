import type { WindArrow, WindDistribution } from "@norain/api/models";

/** The felt-wind fields the arrow and its label read. Nullable so partial data is refused here too. */
export interface FeltWind {
    bearing?: number | null;
    feltSpeed?: number | null;
    feltAngle?: number | null;
}

export function normalizeBearing(degrees: number): number {
    return ((degrees % 360) + 360) % 360;
}

export function apparentArrowBearing(segment: FeltWind): number | null {
    if (segment.bearing == null || segment.feltAngle == null) return null;
    return normalizeBearing(segment.bearing + segment.feltAngle + 180);
}

export function relativeWindLabel(angle: number): string {
    const sectors = ["von vorne", "von vorne rechts", "von rechts", "von hinten rechts",
        "von hinten", "von hinten links", "von links", "von vorne links"];
    return sectors[Math.floor((normalizeBearing(angle) + 22.5) / 45) % 8] ?? "";
}

export function feltWindText(segment: FeltWind): string {
    if (segment.feltSpeed == null) return "Gefühlter Wind nicht verfügbar";
    if (segment.feltAngle == null) return `${Math.round(segment.feltSpeed)} km/h, kein gerichteter Luftzug`;
    return `${Math.round(segment.feltSpeed)} km/h ${relativeWindLabel(segment.feltAngle)} (${Math.round(Math.abs(segment.feltAngle))}°)`;
}

/**
 * Stable route-order thinning, including on loops where non-adjacent points overlap.
 *
 * The server only sends arrows with complete felt-wind data, spaced for the zoom level;
 * this keeps them apart on screen and caps how many the map builds.
 */
export function visibleWindArrows(
    arrows: readonly WindArrow[], project: (arrow: WindArrow) => { x: number; y: number },
): WindArrow[] {
    const kept: WindArrow[] = [];
    const pixels: { x: number; y: number }[] = [];
    for (const arrow of arrows) {
        const point = project(arrow);
        if (!Number.isFinite(point.x) || !Number.isFinite(point.y)
            || pixels.some(p => Math.hypot(p.x - point.x, p.y - point.y) < 80)) continue;
        kept.push(arrow);
        pixels.push(point);
        if (kept.length === 100) break;
    }
    return kept;
}

export function windDistributionParts(distribution: WindDistribution) {
    return [
        { label: "Gegenwind", meters: distribution.headwindM, color: "#d24d78" },
        { label: "Seitenwind", meters: distribution.crosswindM, color: "#2f7fd8" },
        { label: "Rückenwind", meters: distribution.tailwindM, color: "#1a9e8f" },
        { label: "Windstille", meters: distribution.calmM, color: "#b5c3cc" },
        { label: "Unbekannt", meters: distribution.unknownM, color: "#666" },
    ];
}
