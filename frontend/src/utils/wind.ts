import type { WindArrow, WindDistribution } from "@norain/api/models";

/** The ground-wind fields the arrow and its label read. Nullable so partial data is refused here too. */
export interface GroundWind {
    bearing?: number | null;
    windSpeed?: number | null;
    windDir?: number | null;
}

export function normalizeBearing(degrees: number): number {
    return ((degrees % 360) + 360) % 360;
}

/** Map rotation for a real-wind arrow: `windDir` is where the wind comes FROM, the arrow points where it blows TO. */
export function groundArrowBearing(wind: GroundWind): number | null {
    if (wind.windDir == null) return null;
    return normalizeBearing(wind.windDir + 180);
}

export function relativeWindLabel(angle: number): string {
    const sectors = ["von vorne", "von vorne rechts", "von rechts", "von hinten rechts",
        "von hinten", "von hinten links", "von links", "von vorne links"];
    return sectors[Math.floor((normalizeBearing(angle) + 22.5) / 45) % 8] ?? "";
}

export function compassLabel(degrees: number): string {
    const sectors = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
    return sectors[Math.floor((normalizeBearing(degrees) + 22.5) / 45) % 8] ?? "";
}

/** e.g. "12 km/h aus SW, von vorne rechts". */
export function groundWindText(wind: GroundWind): string {
    if (wind.windSpeed == null) return "Wind nicht verfügbar";
    if (wind.windDir == null) return `${Math.round(wind.windSpeed)} km/h, keine Richtung`;
    const base = `${Math.round(wind.windSpeed)} km/h aus ${compassLabel(wind.windDir)}`;
    return wind.bearing == null ? base : `${base}, ${relativeWindLabel(wind.windDir - wind.bearing)}`;
}

/** Extra watts to hold the planned speed against the wind; negative when the wind helps. */
export function windPowerText(watts: number | null | undefined): string {
    if (watts == null || !Number.isFinite(watts)) return "Windaufwand nicht verfügbar";
    const rounded = Math.round(watts);
    if (rounded > 0) return `+${rounded} W Windaufwand (geschätzt)`;
    if (rounded < 0) return `−${-rounded} W, Wind hilft (geschätzt)`;
    return "Kein Windaufwand (geschätzt)";
}

/** Arrow edge length in px: 20 at calm, tailwind or unknown effort, 32 from 230 W up. */
export function windArrowSize(watts: number | null | undefined): number {
    if (watts == null || !Number.isFinite(watts) || watts <= 0) return 20;
    return Math.round(20 + 12 * Math.min(1, watts / 230));
}

/**
 * Stable route-order thinning, including on loops where non-adjacent points overlap.
 *
 * The server only sends arrows with complete wind data, spaced for the zoom level;
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
