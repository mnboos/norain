import type { Data, Layout } from "plotly.js";
import type { ElevationPoint } from "@norain/api/models";

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

export function elevationFigure(
    series: ElevationSeries[],
    axis: "distance" | "time",
): { data: Data[]; layout: Partial<Layout> } {
    const ordered = [...series.filter(s => !s.primary), ...series.filter(s => s.primary)];
    return {
        data: ordered.map(s => ({
            type: "scatter",
            mode: "lines",
            name: s.label,
            x: s.points.map(p => (axis === "distance" ? p.distanceM / 1000 : p.elapsedS / 60)),
            y: smoothElevation(s.points),
            connectgaps: false,
            opacity: s.primary ? 1 : 0.85,
            line: { color: s.color, width: s.primary ? 3.5 : 2, shape: "spline", smoothing: 0 },
            hovertemplate: `%{y:.0f} m<extra>${s.label}</extra>`,
        })),
        layout: {
            showlegend: false,
            uirevision: `elevation-${axis}`,
            xaxis: {
                title: { text: axis === "distance" ? "Strecke (km)" : "Fahrzeit (min)" },
                rangemode: "tozero",
                showgrid: false,
            },
            yaxis: { title: { text: "Höhe (m ü. M.)" }, autorange: true },
        },
    };
}
