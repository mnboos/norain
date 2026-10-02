import { ref, watch, watchEffect, type WatchStopHandle } from "vue";
import type { IControl, Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import lightStyleUrl from "@/assets/map-styles/positron.json?url";
import darkStyleUrl from "@/assets/map-styles/dark-matter.json?url";

/**
 * The basemap under every map: CARTO's pale vector map (Positron / Dark Matter by theme) or
 * swisstopo's SWISSIMAGE aerial photos. One choice per browser, shared by every open map.
 *
 * SWISSIMAGE is Swiss open government data (commercial use allowed, attribution required) and
 * covers Switzerland only: the source's `bounds` keep MapLibre from asking for tiles outside it.
 * On imagery the labels are Dark Matter's (light text, dark halo), and the route colours take
 * their dark-map variants (`isDarkMap`).
 */
export type Basemap = "map" | "satellite";

const BASEMAP_KEY = "norain.basemap";

export const SWISSIMAGE_TILES =
    "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg";
export const SWISSIMAGE_BOUNDS: [number, number, number, number] = [5.9, 45.8, 10.5, 47.9];
const SATELLITE_SOURCE = "swissimage";

export function readStoredBasemap(): Basemap {
    try {
        if (localStorage.getItem(BASEMAP_KEY) === "satellite") return "satellite";
    } catch {
        // Storage blocked (private window, previews): fall through to the default.
    }
    return "map";
}

const basemap = ref<Basemap>(readStoredBasemap());
watch(basemap, kind => {
    try {
        localStorage.setItem(BASEMAP_KEY, kind);
    } catch {
        // Not remembered; the toggle still works for this page.
    }
});

export function useBasemap() {
    return basemap;
}

/** Imagery reads like a dark map: the route line, casing and wind use their dark colours. */
export function isDarkMap(kind: Basemap, dark: boolean): boolean {
    return dark || kind === "satellite";
}

/** Dark Matter's labels over SWISSIMAGE: everything but the symbol layers is dropped. */
export function satelliteStyle(labels: StyleSpecification): StyleSpecification {
    return {
        ...labels,
        sources: {
            ...labels.sources,
            [SATELLITE_SOURCE]: {
                type: "raster",
                tiles: [SWISSIMAGE_TILES],
                tileSize: 256,
                maxzoom: 20,
                bounds: SWISSIMAGE_BOUNDS,
                attribution: '© <a href="https://www.swisstopo.admin.ch/" target="_blank">swisstopo</a>',
            },
        },
        layers: [
            { id: "satellite-background", type: "background", paint: { "background-color": "#1d1f20" } },
            { id: "satellite", type: "raster", source: SATELLITE_SOURCE },
            ...labels.layers.filter(layer => layer.type === "symbol"),
        ],
    };
}

/**
 * Load the basemap into `map`, replacing whatever style it has, and call `onReady` once the
 * new style is in. setStyle() drops every source and layer the page added, so `onReady` is
 * where they go back (DOM markers and popups survive).
 */
export function applyBasemap(map: MapLibreMap, kind: Basemap, dark: boolean, onReady?: () => void) {
    if (kind === "satellite") {
        map.setStyle(darkStyleUrl, { diff: false, transformStyle: (_previous, next) => satelliteStyle(next) });
    } else {
        map.setStyle(dark ? darkStyleUrl : lightStyleUrl, { diff: false });
    }
    if (onReady) map.once("style.load", onReady);
}

const MAP_ICON =
    "M15,19L9,16.89V5L15,7.11M20.5,3C20.44,3 20.39,3 20.34,3L15,5.1L9,3L3.36,4.9C3.15,4.97 3,5.15 3,5.38V20.5A0.5,0.5 0 0,0 3.5,21C3.55,21 3.61,21 3.66,20.97L9,18.9L15,21L20.64,19.1C20.85,19 21,18.85 21,18.62V3.5A0.5,0.5 0 0,0 20.5,3Z";
const SATELLITE_ICON = "M14,6L10.25,11L13.1,14.8L11.5,16C9.81,13.75 7,10 7,10L1,18H23L14,6Z";

/**
 * A map button that switches the shared basemap. It shows the view it switches *to*.
 * `label(next)` words the button and is read inside an effect, so a language switch relabels it.
 */
export class BasemapControl implements IControl {
    private container: HTMLDivElement | undefined;
    private stop: WatchStopHandle | undefined;

    constructor(private readonly label: (next: Basemap) => string) {}

    onAdd(): HTMLElement {
        const container = document.createElement("div");
        container.className = "maplibregl-ctrl maplibregl-ctrl-group";
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.testid = "basemap-toggle";
        const svgNs = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(svgNs, "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("width", "20");
        svg.setAttribute("height", "20");
        svg.setAttribute("aria-hidden", "true");
        svg.style.display = "block";
        svg.style.margin = "auto";
        const path = document.createElementNS(svgNs, "path");
        path.setAttribute("fill", "#333");
        svg.appendChild(path);
        button.appendChild(svg);
        button.addEventListener("click", () => {
            basemap.value = basemap.value === "satellite" ? "map" : "satellite";
        });
        container.appendChild(button);
        this.stop = watchEffect(() => {
            const next: Basemap = basemap.value === "satellite" ? "map" : "satellite";
            const text = this.label(next);
            button.title = text;
            button.setAttribute("aria-label", text);
            path.setAttribute("d", next === "satellite" ? SATELLITE_ICON : MAP_ICON);
        });
        this.container = container;
        return container;
    }

    onRemove(): void {
        this.stop?.();
        this.container?.remove();
        this.container = undefined;
    }
}
