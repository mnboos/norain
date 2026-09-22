/**
 * The journey planner's POI categories (backend core/pois.py POI_RULES), as the UI names and
 * draws them. Colours are for telling categories apart on the map only; every marker also
 * carries its name in a popup, so colour is never the only channel.
 */
export interface PoiCategory {
    value: string;
    label: string;
    emoji: string;
    color: string;
}

export const POI_CATEGORIES: PoiCategory[] = [
    { value: "drinking_water", label: "Trinkwasser", emoji: "🚰", color: "#1f77b4" },
    { value: "toilets", label: "Toilette", emoji: "🚻", color: "#7f7f7f" },
    { value: "shelter", label: "Unterstand", emoji: "⛺", color: "#8c564b" },
    { value: "food", label: "Essen", emoji: "🍽️", color: "#d62728" },
    { value: "groceries", label: "Einkauf", emoji: "🛒", color: "#ff7f0e" },
    { value: "vending_machine", label: "Automat", emoji: "🥤", color: "#bcbd22" },
    { value: "bbq", label: "Grillstelle", emoji: "🔥", color: "#e377c2" },
    { value: "bike_repair", label: "Veloreparatur", emoji: "🔧", color: "#17becf" },
    { value: "ebike_charging", label: "E-Bike-Laden", emoji: "🔌", color: "#2ca02c" },
    { value: "train_station", label: "Bahnhof", emoji: "🚉", color: "#9467bd" },
    { value: "lodging", label: "Unterkunft", emoji: "🛏️", color: "#393b79" },
];

/** Where a day may end: tourism=* values (backend core/pois.py LODGING_KINDS). */
export const LODGING_KINDS = [
    { value: "camp_site", label: "Camping" },
    { value: "hostel", label: "Hostel" },
    { value: "guest_house", label: "Pension" },
    { value: "hotel", label: "Hotel" },
    { value: "alpine_hut", label: "Berghütte" },
    { value: "wilderness_hut", label: "Unbewartete Hütte" },
];

const BY_VALUE = new Map(POI_CATEGORIES.map(category => [category.value, category]));

export function poiCategory(value: string): PoiCategory {
    return BY_VALUE.get(value) ?? { value, label: value, emoji: "📍", color: "#555555" };
}

/** A POI's name, or its category when OSM has none (OSM's empty name arrives as ""). */
export function poiName(poi: { name?: string; category: string }): string {
    return poi.name?.length ? poi.name : poiCategory(poi.category).label;
}

/** A point of interest drawn over the route on NiceMap. `emphasis` marks planned stops. */
export interface MapPoi {
    lon: number;
    lat: number;
    category: string;
    name?: string;
    emphasis?: boolean;
}
