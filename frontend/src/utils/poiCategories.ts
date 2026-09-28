import { t, te } from "@/i18n";

/**
 * The journey planner's POI categories (backend core/pois.py POI_RULES), as the UI names and
 * draws them. Colours are for telling categories apart on the map only; every marker also
 * carries its name in a popup, so colour is never the only channel.
 *
 * `label` is a getter: it is worded when read, in the current language, so these constants
 * never keep the language the page loaded with. Don't spread one to keep its label.
 */
export interface PoiCategory {
    readonly value: string;
    readonly label: string;
    readonly emoji: string;
    readonly color: string;
}

function category(value: string, emoji: string, color: string): PoiCategory {
    return {
        value,
        emoji,
        color,
        get label() {
            const key = `poi.category.${value}`;
            return te(key) ? t(key) : value;
        },
    };
}

export const POI_CATEGORIES: PoiCategory[] = [
    category("drinking_water", "🚰", "#1f77b4"),
    category("toilets", "🚻", "#7f7f7f"),
    category("shelter", "⛺", "#8c564b"),
    category("food", "🍽️", "#d62728"),
    category("groceries", "🛒", "#ff7f0e"),
    category("vending_food", "🥪", "#bcbd22"),
    category("vending_drinks", "🥤", "#aec7e8"),
    category("vending_sweets", "🍫", "#f7b6d2"),
    category("vending_coffee", "☕", "#c49c94"),
    category("bbq", "🔥", "#e377c2"),
    category("bike_repair", "🔧", "#17becf"),
    category("ebike_charging", "🔌", "#2ca02c"),
    category("train_station", "🚉", "#9467bd"),
    category("lodging", "🛏️", "#393b79"),
];

function lodgingKind(value: string, emoji: string) {
    return {
        value,
        emoji,
        get label() {
            return t(`poi.lodging.${value}`);
        },
    };
}

/** Where a day may end: tourism=* values (backend core/pois.py LODGING_KINDS). */
export const LODGING_KINDS = [
    lodgingKind("camp_site", "⛺"),
    lodgingKind("hostel", "🛏️"),
    lodgingKind("guest_house", "🏡"),
    lodgingKind("hotel", "🏨"),
    lodgingKind("alpine_hut", "🏔️"),
    lodgingKind("wilderness_hut", "🛖"),
];

/** Drawn on the map like a POI but never offered to the journey planner. */
export const PHOTO_CATEGORY: PoiCategory = category("photo", "📷", "#6a3d9a");

const BY_VALUE = new Map([...POI_CATEGORIES, PHOTO_CATEGORY].map(category => [category.value, category]));

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
