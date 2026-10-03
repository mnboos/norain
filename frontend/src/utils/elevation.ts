import type { Data, Layout } from "plotly.js";
import type { ElevationPoint } from "@norain/api/models";

import { t, tp } from "@/i18n";
import { chartLayout } from "@/utils/chartLayout";

export interface ElevationSeries {
    points: ElevationPoint[];
    color: string;
    label: string;
    /** The route the page is about: drawn last (on top) and thicker than the others. */
    primary?: boolean;
}

/** How far either side a drawn height averages over. Display only: a gentle calming of the
 * terrain model's point-to-point jitter, not a change to the data. */
export const ELEVATION_SMOOTHING_M = 80;

/** Sum every climb and descent, rather than just the difference between the endpoints.
 * A complete profile is required so gaps cannot silently undercount the route's totals. */
export function elevationTotals(points: ElevationPoint[]): { ascentM: number; descentM: number } | null {
    if (points.length < 2) return null;
    let ascentM = 0;
    let descentM = 0;
    let previous: number | null = null;
    for (const point of points) {
        const height = point.elevationM;
        if (height == null || !Number.isFinite(height)) return null;
        if (previous != null) {
            const change = height - previous;
            if (change > 0) ascentM += change;
            else descentM -= change;
        }
        previous = height;
    }
    return { ascentM: Math.round(ascentM), descentM: Math.round(descentM) };
}

/**
 * Each height as a distance-weighted (triangular) mean of its neighbours within `radiusM`.
 * Never across a gap: a missing height ends the run, and stays missing.
 */
export function smoothElevation(points: ElevationPoint[], radiusM = ELEVATION_SMOOTHING_M): (number | null)[] {
    return points.map((point, i) => {
        if (point.elevationM == null || radiusM <= 0) return point.elevationM ?? null;
        let sum = 0;
        let weights = 0;
        for (const step of [-1, 1]) {
            for (let j = step === -1 ? i : i + 1; ; j += step) {
                const neighbour = points[j];
                const height = neighbour?.elevationM;
                if (neighbour === undefined || height == null) {
                    break;
                }
                const offset = Math.abs(neighbour.distanceM - point.distanceM);
                if (offset >= radiusM) break;
                const weight = 1 - offset / radiusM;
                sum += weight * height;
                weights += weight;
            }
        }
        return sum / weights;
    });
}

export const ELEVATION_PRIMARY_GROUP = "primary";

export function elevationFigure(
    series: ElevationSeries[],
    axis: "distance" | "time",
    profile?: string | null,
): { data: Data[]; layout: Partial<Layout> } {
    const ordered = [...series.filter(s => !s.primary), ...series.filter(s => s.primary)];
    const layout = chartLayout(
        t("elevation.title"),
        t("charts.axis.elevation"),
        axis === "distance" ? t("charts.axis.distanceKm") : tp(profile, "charts.axis.rideMinutes"),
    );
    return {
        data: ordered.map(s => ({
            type: "scatter",
            mode: "lines",
            name: s.label,
            // The selected position is marked on the route itself, not at the same km of another.
            legendgroup: s.primary ? ELEVATION_PRIMARY_GROUP : undefined,
            x: s.points.map(p => (axis === "distance" ? p.distanceM / 1000 : p.elapsedS / 60)),
            y: smoothElevation(s.points),
            connectgaps: false,
            opacity: s.primary ? 1 : 0.85,
            line: { color: s.color, width: s.primary ? 3.5 : 2, shape: "spline", smoothing: 0 },
            hovertemplate: `%{y:.0f} m<extra>${s.label}</extra>`,
        })),
        layout: {
            ...layout,
            uirevision: `elevation-${axis}`,
            yaxis: { ...layout.yaxis, zeroline: false, autorange: true },
        },
    };
}
