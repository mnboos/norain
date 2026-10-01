/**
 * The providers a forecast drew on, for the attribution under it. Open-Meteo and MET Norway
 * publish under CC BY 4.0, which asks for credit and a link wherever their data is shown.
 */
export interface WeatherProvider {
    name: string;
    url: string;
}

const PROVIDERS: Record<string, WeatherProvider> = {
    "open-meteo": { name: "Open-Meteo", url: "https://open-meteo.com/" },
    "open-meteo-ensemble": { name: "Open-Meteo", url: "https://open-meteo.com/" },
    "met-norway": { name: "MET Norway", url: "https://www.met.no/en" },
    openweathermap: { name: "OpenWeatherMap", url: "https://openweathermap.org/" },
};

/** `summary.sources`, or the single `summary.source` of a result stored before `sources` existed. */
export function weatherProviders(summary: { source: string; sources?: string[] }): WeatherProvider[] {
    const sources = summary.sources?.length ? summary.sources : [summary.source];
    const seen = new Map<string, WeatherProvider>();
    for (const source of sources) {
        const provider = PROVIDERS[source];
        if (provider && !seen.has(provider.name)) seen.set(provider.name, provider);
    }
    return [...seen.values()];
}
