/** Shared visual settings for elevation and weather charts. Dimensions are in pixels. */
export const CHART_STYLE = {
    height: { normal: 220, compact: 180 },
    breakpoints: { compactWidth: 260, compactHeight: 220, narrowWidth: 520 },
    font: { title: 14, compactTitle: 11, legend: 10, baseLegend: 11, compactTick: 9, bandLabel: 10 },
    title: { top: 0.98, paddingTop: 10 },
    margin: { top: 48, narrowTop: 96, bottom: 48, side: 44 },
    legend: { top: 1.02, itemWidth: 30 },
    axis: {
        xTitleStandoff: 4,
        yTitleStandoff: 15,
        /** Share of the final plot width reserved at each end of a line. */
        horizontalInsetFraction: 0.05,
        /** Half-range fallback for a single x value, in that axis's units. */
        singlePointMinHalfRange: 0.5,
        temperaturePaddingFraction: 0.08,
        temperatureMinPaddingC: 0.5,
    },
    line: { width: 1.5, isolatedPointSize: 4 },
    selection: { ruleOpacity: 0.35, ruleWidth: 1, pointRadius: 5, pointBorderWidth: 2 },
} as const;
