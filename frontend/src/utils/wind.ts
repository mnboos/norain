import type { WindArrow, WindDistribution } from "@norain/api/models";

import { t } from "@/i18n";
import { windEffortText } from "@/utils/levels";

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

function sector(degrees: number): number {
    return Math.floor((normalizeBearing(degrees) + 22.5) / 45) % 8;
}

/** "von vorne rechts" …: eight sectors relative to the direction of travel. */
export function relativeWindLabel(angle: number): string {
    return t(`wind.relative.${sector(angle)}`);
}

export function compassLabel(degrees: number): string {
    return t(`wind.compass.${sector(degrees)}`);
}

/** e.g. "12 km/h aus SW, von vorne rechts". */
export function groundWindText(wind: GroundWind): string {
    if (wind.windSpeed == null) return t("wind.notAvailable");
    const speed = Math.round(wind.windSpeed);
    if (wind.windDir == null) return t("wind.noDirection", { speed });
    const base = t("wind.from", { speed, dir: compassLabel(wind.windDir) });
    return wind.bearing == null ? base : `${base}, ${relativeWindLabel(wind.windDir - wind.bearing)}`;
}

/**
 * Extra effort to hold the planned speed against the wind, as the level the server chose
 * (`windEffortLevel`: "tailwind", "none", "low" … "very_high"). The thresholds are part of
 * the ride-quality scoring and stay on the server.
 */
export function windPowerText(level: string | null | undefined): string {
    if (level == null) return t("wind.effort.notAvailable");
    if (level === "tailwind") return t("wind.effort.tailwind");
    if (level === "none") return t("wind.effort.none");
    return t("wind.effort.level", { level: windEffortText(level) });
}

/** Arrow edge length in px from the server's 0..1 `windEffort`: 20 at calm, tailwind or unknown, 32 at the top. */
export function windArrowSize(effort: number | null | undefined): number {
    if (effort == null || !Number.isFinite(effort) || effort <= 0) return 20;
    return Math.round(20 + 12 * Math.min(1, effort));
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
        { label: t("wind.distribution.headwind"), meters: distribution.headwindM, color: "#d24d78" },
        { label: t("wind.distribution.crosswind"), meters: distribution.crosswindM, color: "#2f7fd8" },
        { label: t("wind.distribution.tailwind"), meters: distribution.tailwindM, color: "#1a9e8f" },
        { label: t("wind.distribution.calm"), meters: distribution.calmM, color: "#b5c3cc" },
        { label: t("wind.distribution.unknown"), meters: distribution.unknownM, color: "#666" },
    ];
}
