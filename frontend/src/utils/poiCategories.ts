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
    { value: "vending_food", label: "Automat: Essen", emoji: "🥪", color: "#bcbd22" },
    { value: "vending_drinks", label: "Automat: Getränke", emoji: "🥤", color: "#aec7e8" },
    { value: "vending_sweets", label: "Automat: Süsses", emoji: "🍫", color: "#f7b6d2" },
    { value: "vending_coffee", label: "Automat: Kaffee", emoji: "☕", color: "#c49c94" },
    { value: "bbq", label: "Grillstelle", emoji: "🔥", color: "#e377c2" },
    { value: "bike_repair", label: "Veloreparatur", emoji: "🔧", color: "#17becf" },
    { value: "ebike_charging", label: "E-Bike-Laden", emoji: "🔌", color: "#2ca02c" },
    { value: "train_station", label: "Bahnhof", emoji: "🚉", color: "#9467bd" },
    { value: "lodging", label: "Unterkunft", emoji: "🛏️", color: "#393b79" },
];

/** Where a day may end: tourism=* values (backend core/pois.py LODGING_KINDS). */
export const LODGING_KINDS = [
    { value: "camp_site", label: "Camping", emoji: "⛺" },
    { value: "hostel", label: "Hostel", emoji: "🛏️" },
    { value: "guest_house", label: "Pension", emoji: "🏡" },
    { value: "hotel", label: "Hotel", emoji: "🏨" },
    { value: "alpine_hut", label: "Berghütte", emoji: "🏔️" },
    { value: "wilderness_hut", label: "Unbewartete Hütte", emoji: "🛖" },
];

const BY_VALUE = new Map(POI_CATEGORIES.map(category => [category.value, category]));

export function poiCategory(value: string): PoiCategory {
    return BY_VALUE.get(value) ?? { value, label: value, emoji: "📍", color: "#555555" };
}

/** A POI's name, or its category when OSM has none (OSM's empty name arrives as ""). */
export function poiName(poi: { name?: string; category: string }): string {
    return poi.name?.length ? poi.name : poiCategory(poi.category).label;
}

/**
 * A point of interest drawn over the route on NiceMap. A `planned` one (a break, a detour, the
 * night's lodging) is drawn in its category colour; any other is a candidate, drawn in
 * `CANDIDATE_COLOR`. Its popup says which, so colour is never the only channel.
 */
export interface MapPoi {
    osmRef: string;
    lon: number;
    lat: number;
    category: string;
    name?: string;
    planned: boolean;
    /** What the popup says about a grey one instead of "nicht eingeplant", e.g. "Pause in Variante 2". */
    note?: string;
}

/** Candidates are grey: one mid-grey that reads on both the light and the dark basemap. */
export const CANDIDATE_COLOR = "#8a8a8a";
