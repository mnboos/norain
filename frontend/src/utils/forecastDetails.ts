import {
    RouteWeatherSummaryMaxFrostLevelEnum as Frost,
    type EnsembleRange,
    type ForecastSampleOut,
    type RouteForecastOut,
    type RouteWeatherSummary,
} from "@norain/api/models";

import { intlLocale, t } from "@/i18n";

export function swissTime(iso: string): string {
    // Provider/route timestamps without an offset are already Swiss local time.
    if (!/(Z|[+-]\d\d:\d\d)$/.test(iso)) return iso.slice(11, 16);
    return new Date(iso).toLocaleTimeString(intlLocale(), {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Europe/Zurich",
    });
}

// Largest rain rate of the deterministic main run, rounded the way the card displays it.
function mainRunPeakRate(samples: ForecastSampleOut[]): number | null {
    const rates = samples.flatMap(s => (s.rainRateMmH == null ? [] : [s.rainRateMmH]));
    return rates.length ? Math.round(Math.max(...rates) * 10) / 10 : null;
}

// Wind effort levels worth a headline of their own; "low" and "none" are not.
const DRY_WIND_HEADLINES = new Set(["very_high", "high", "medium", "tailwind"]);

/**
 * With rain ruled out, "trocken" alone says little: name what the rider will feel instead.
 * Frost comes first because it is a safety matter, the wind second. Both are levels the
 * server already worked out; this only turns the codes into a sentence.
 */
function dryHeadline(summary: RouteWeatherSummary): string {
    const frost = summary.maxFrostLevel;
    if (frost) return frost === Frost.Light ? t("forecast.headline.dryLightIce") : t("forecast.headline.dryIce");
    const wind = summary.maxWindEffortLevel;
    return wind && DRY_WIND_HEADLINES.has(wind) ? t(`forecast.headline.dryWind.${wind}`) : t("forecast.headline.dry");
}

export function forecastHeadline(summary: RouteWeatherSummary, samples: ForecastSampleOut[]): string {
    if (!samples.length) return t("forecast.headline.noData");
    const p = summary.rainProbability;
    if (p == null) return summary.willRain ? t("forecast.headline.rainExpected") : dryHeadline(summary);
    // `willRain` is the backend's ensemble verdict (POP_VERDICT). A few wet members below it
    // are shown as the risk percentage, not as a headline next to a dry "Regen" figure. The
    // main run raining still counts, or the headline would say dry beside a non-zero rate.
    if (!summary.willRain && !(mainRunPeakRate(samples) ?? 0)) return dryHeadline(summary);
    return summary.firstRainEta
        ? t("forecast.headline.rainPossibleFrom", { time: swissTime(summary.firstRainEta) })
        : t("forecast.headline.rainPossible");
}

/**
 * The "Regen" figure in the key ride data, from the same forecast as the headline: when the ensemble expects
 * rain, the amount its wet members predict (`rainAmount`, mm/h at the peak-risk point) —
 * the main run is a single scenario and is often dry exactly where the members are not.
 */
/**
 * The ride's felt temperature (wind chill at riding speed), averaged over riding time: each
 * sample stands for half the time to each neighbour. A sample without `feltTemp` - no timing,
 * or a forecast stored before it existed - counts with its air temperature.
 */
export function meanFeltTemp(samples: readonly Pick<ForecastSampleOut, "elapsedS" | "temp" | "feltTemp">[]): number | null {
    const points = samples.map(s => [s.elapsedS, s.feltTemp ?? s.temp] as const);
    if (!points.length) return null;
    let weighted = 0;
    let total = 0;
    points.reduce((prev, point) => {
        const gap = Math.max(0, point[0] - prev[0]);
        weighted += (gap * (point[1] + prev[1])) / 2;
        total += gap;
        return point;
    });
    return total > 0 ? weighted / total : points.reduce((sum, [, value]) => sum + value, 0) / points.length;
}

export function peakRain(forecast: RouteForecastOut): string | null {
    const { summary, samples } = forecast;
    const mainRun = mainRunPeakRate(samples);
    if (summary.rainProbability != null && summary.willRain) {
        return Math.max(summary.rainAmount, mainRun ?? 0).toFixed(1);
    }
    return mainRun == null ? null : mainRun.toFixed(1);
}

export function peakRisk(forecast: RouteForecastOut): string {
    const p = forecast.summary.rainProbability;
    return p == null ? t("forecast.notAvailable") : `${Math.round(p * 100)}%`;
}

export function rangeText(range: EnsembleRange | undefined, unit: string): string {
    if (range?.median == null || range.p10 == null || range.p90 == null) return t("forecast.notAvailable");
    return `${range.p10.toFixed(1)}–${range.p90.toFixed(1)} ${unit}`;
}

const METRICS = [
    { key: "precipitation", unit: "mm/h" },
    { key: "temperature", unit: "°C" },
    { key: "windSpeed", unit: "km/h" },
    { key: "windGust", unit: "km/h" },
    { key: "headwind", unit: "km/h" },
    { key: "crosswind", unit: "km/h" },
] as const;

/** The metrics of the details panel, labelled in the current language (call it in a computed). */
export function metricLabels() {
    return METRICS.map(metric => ({ ...metric, label: t(`forecast.metric.${metric.key}`) }));
}

const modelLabels: Record<string, string> = {
    icon_seamless_eps: "DWD ICON Seamless",
    meteoswiss_icon_ch1_ensemble: "MeteoSwiss ICON CH1",
    meteoswiss_icon_ch2_ensemble: "MeteoSwiss ICON CH2",
};

export function modelLabel(model: string): string {
    return modelLabels[model] ?? model;
}
