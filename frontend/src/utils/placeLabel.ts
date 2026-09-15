import type { PlacesSearchResult } from "@norain/api/models";

const cantonAbbreviations: Record<string, string> = {
    Aargau: "AG",
    "Appenzell Innerrhoden": "AI",
    "Appenzell Ausserrhoden": "AR",
    Bern: "BE",
    "Basel-Landschaft": "BL",
    "Basel-Stadt": "BS",
    Fribourg: "FR",
    Genève: "GE",
    Glarus: "GL",
    Graubünden: "GR",
    Jura: "JU",
    Luzern: "LU",
    Neuchâtel: "NE",
    Nidwalden: "NW",
    Obwalden: "OW",
    "St. Gallen": "SG",
    Schaffhausen: "SH",
    Solothurn: "SO",
    Schwyz: "SZ",
    Thurgau: "TG",
    Ticino: "TI",
    Uri: "UR",
    Vaud: "VD",
    Valais: "VS",
    Zug: "ZG",
    Zürich: "ZH",
};

export function cityWithOptionalCanton(feature: PlacesSearchResult): string {
    const { city, state, showCanton } = feature.properties;
    if (!city) return "";

    if (showCanton) {
        const cantonAbbr = state ? cantonAbbreviations[state] : undefined;
        if (cantonAbbr) {
            return `${city} (${cantonAbbr})`;
        }
    }
    return city;
}

/** One-line label for a place: the text shown in the select's input once it is chosen. */
export function placeLabel(feature: PlacesSearchResult): string {
    const { name, city } = feature.properties;
    if (!name) return "Unknown";

    const cityWithCanton = cityWithOptionalCanton(feature);

    if (name === city) {
        return cityWithCanton;
    }
    return cityWithCanton ? `${name}, ${cityWithCanton}` : name;
}

/** Caption under the name in the dropdown option. */
export function placeSecondaryLine(feature: PlacesSearchResult): string {
    const { name, city, state } = feature.properties;

    if (name && name === city) {
        return state ?? "";
    }
    if (city && name !== city) {
        return cityWithOptionalCanton(feature);
    }
    return "";
}
