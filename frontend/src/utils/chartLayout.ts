import type { Layout } from "plotly.js";
import { CHART_STYLE } from "@/utils/chartStyle";

/** Shared chart chrome; NiceChart adds the responsive sizing and light/dark theme. */
export function chartLayout(title: string, unit: string, xTitle: string): Partial<Layout> {
    return {
        title: {
            text: title,
            font: { size: CHART_STYLE.font.title },
            x: 0,
            xref: "paper",
            xanchor: "left",
            y: 1,
            yref: "container",
            yanchor: "top",
            pad: { t: CHART_STYLE.title.paddingTop },
        },
        autosize: true,
        hovermode: "closest",
        showlegend: true,
        xaxis: {
            title: { text: xTitle, standoff: CHART_STYLE.axis.xTitleStandoff },
            zeroline: false,
            automargin: true,
        },
        yaxis: {
            title: { text: unit, standoff: CHART_STYLE.axis.yTitleStandoff },
            zeroline: true,
            automargin: true,
        },
        legend: {
            orientation: "h",
            font: { size: CHART_STYLE.font.baseLegend },
            itemwidth: CHART_STYLE.legend.itemWidth,
            tracegroupgap: 0,
            bgcolor: "rgba(0,0,0,0)",
        },
    };
}
