<script setup lang="ts">
import { Map as MapLibreMap, Marker, Popup, LngLatBounds, GeoJSONSource, config as maplibreConfig } from "maplibre-gl";
import type { ExpressionSpecification } from "maplibre-gl";
// maplibre-gl 6 is pure ESM and loads its worker as a sibling file resolved from
// `import.meta.url`. Once the bundler inlines maplibre into a chunk that path no longer
// exists, the worker dies silently and anything the worker parses (our GeoJSON route)
// never loads - raster tiles still paint, so it looks like the route just disappeared.
// Point maplibre at the worker the bundler emits for us instead.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { computed, onBeforeUnmount, onMounted, type Ref, ref, useTemplateRef, watch, toRefs } from "vue";
import { useQuasar } from "quasar";

import "maplibre-gl/dist/maplibre-gl.css";
import type { PlacesSearchResult, RouteWeatherOut, WeatherSample } from "@norain/api/models";
import { isNightEta, pickVisibleSamples, weatherIconSvg } from "@/utils/weatherIcons";
import { swissTime } from "@/utils/forecastDetails";
import { gradientStops, rideScore, rideScoreLabel, sampleProgress, scoreBand, scoreColor } from "@/utils/rideQuality";
import MapLegend from "@/components/MapLegend.vue";

maplibreConfig.WORKER_URL = maplibreWorkerUrl;

const $q = useQuasar();

// Pale, low-ink vector basemaps (CARTO, no API key) so the route line and the weather chips
// carry the map instead of competing with OSM's POIs and landuse fills. Both styles ship
// their own OSM/CARTO attribution.
const LIGHT_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";
const DARK_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

const props = defineProps<{
    routeWeather: RouteWeatherOut | undefined;
    abfahrtsort?: PlacesSearchResult;
    zielort?: PlacesSearchResult;
    /** CSS height of the map canvas. Defaults to the full viewport. */
    height?: string;
    selectedSample?: number;
}>();

const { routeWeather, abfahrtsort, zielort } = toRefs(props);

const emit = defineEmits<{
    mapView: [view: { zoom: number; lat: number; lng: number }];
    selectSample: [index: number];
}>();

const mapContainer = useTemplateRef<HTMLDivElement>("map");
const mymap: Ref<MapLibreMap | undefined> = ref(undefined);
const webglError = ref<string | undefined>(undefined);

const hasMap = computed(() => !!mymap.value);

function emitMapView(map: MapLibreMap) {
    const { lat, lng } = map.getCenter();
    emit("mapView", { zoom: map.getZoom(), lat, lng });
}

// --- start / destination markers ---
let startMarker: Marker | undefined;
let destMarker: Marker | undefined;
let selectedMarker: Marker | undefined;

function highlightSample() {
    const map = mymap.value;
    const sample = props.routeWeather?.samples[props.selectedSample ?? -1];
    if (!map || !sample) {
        selectedMarker?.remove();
        return;
    }
    if (!selectedMarker) {
        const element = document.createElement("div");
        element.className = "wx-selected";
        element.dataset.testid = "selected-map-sample";
        element.setAttribute("role", "img");
        selectedMarker = new Marker({ element });
    }
    selectedMarker.getElement().setAttribute("aria-label", `Ausgewählter Punkt: ${swissTime(sample.eta)} Uhr`);
    selectedMarker.setLngLat([sample.lon, sample.lat]).addTo(map);
}
watch([() => props.selectedSample, routeWeather, hasMap], highlightSample);

function placeMarker(
    existing: Marker | undefined,
    place: PlacesSearchResult | undefined,
    color: string,
): Marker | undefined {
    const map = mymap.value;
    if (!map) return existing;
    existing?.remove();
    if (!place) return undefined;
    const [lon, lat] = place.geometry.coordinates;
    return new Marker({ color }).setLngLat([lon ?? 0, lat ?? 0]).addTo(map);
}

watch([abfahrtsort, hasMap], () => {
    startMarker = placeMarker(startMarker, abfahrtsort.value, "#2b6cb0");
});
watch([zielort, hasMap], () => {
    destMarker = placeMarker(destMarker, zielort.value, "#d24d78");
});

// The route line is colored by ride quality (see utils/rideQuality.ts). Spectral's middle
// steps are very pale (#fee08b, #e6f598 sit near 1.2:1 against the light basemap), so a
// white casing would let them vanish into CARTO Positron - the casing carries the ink
// instead and flips with the theme. Same values NiceChart.vue uses for chart ink.
const CASING_LIGHT = "#1b2733";
const CASING_DARK = "#e8eef2";

const scores = computed(() => (routeWeather.value?.samples ?? []).map(s => rideScore(s)?.score ?? null));
const hasRoute = computed(() => (routeWeather.value?.samples.length ?? 0) > 0);
/** Only claim a "Keine Daten" swatch when a stretch really is unknown. */
const hasMissingScores = computed(() => scores.value.some(v => v === null));

// --- sample marker: weather chip (condition glyph + temperature) with the wind arrow
// attached beside it. The arrow points in the direction the wind blows TOWARD. ---
function sampleMarkerEl(sample: WeatherSample): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "wx-marker";
    const strong = sample.headwind > 8;
    const glyph = weatherIconSvg(sample.weatherCode, {
        rainMm: sample.rainMm,
        night: isNightEta(sample.eta),
    });
    // wind_dir is the direction the wind comes FROM; blowing-toward = +180°. Only the arrow
    // rotates - rotating the whole marker would tip the temperature text over with it.
    el.innerHTML = `
        <svg class="wx-wind" width="16" height="16" viewBox="0 0 24 24"
             style="transform:rotate(${sample.windDir + 180}deg)">
            <path d="M12 2 L17 13 L12 10.5 L7 13 Z"
                  fill="${strong ? "#d24d78" : "#2c3e50"}" stroke="white" stroke-width="1.5"/>
        </svg>
        <div class="wx-chip" style="border-color:${scoreColor(rideScore(sample)?.score ?? null)}">
            <svg width="20" height="20" viewBox="0 0 24 24">${glyph}</svg>
            <span>${Math.round(sample.temp)}°</span>
        </div>`;
    return el;
}

let sampleMarkers: { marker: Marker; popup: Popup; sample: WeatherSample }[] = [];

function clearSampleMarkers() {
    sampleMarkers.forEach(({ marker, popup }) => {
        popup.remove();
        marker.remove();
    });
    sampleMarkers = [];
}

/** Hide chips that would overlap at the current zoom. Cheaper than re-creating markers,
 *  and it keeps the popups alive. */
function applyMarkerThinning() {
    const map = mymap.value;
    if (!map || sampleMarkers.length === 0) return;
    const samples = sampleMarkers.map(m => m.sample);
    // Carry the quality band in, so a stretch that turns bad through wind or cold keeps a
    // chip at the transition - the line's colour is never the only cue.
    const thinnable = samples.map(s => ({ rainMm: s.rainMm, band: scoreBand(rideScore(s)?.score ?? null) }));
    const visible = pickVisibleSamples(thinnable, i => {
        const s = samples[i];
        return map.project([s?.lon ?? 0, s?.lat ?? 0]);
    });
    sampleMarkers.forEach(({ marker }, i) => {
        marker.getElement().classList.toggle("wx-marker--hidden", !visible.has(i));
    });
}

function fmtTime(iso: string): string {
    return swissTime(iso);
}

function windText(s: WeatherSample): string {
    if (s.headwind > 1) return `${Math.round(s.headwind)} km/h Gegenwind`;
    if (s.headwind < -1) return `${Math.round(-s.headwind)} km/h Rückenwind`;
    return "Seitenwind";
}

async function renderRoute(rw: RouteWeatherOut) {
    const map = mymap.value;
    if (!map) return;

    // 1) Route line, colored along its length by ride quality via line-gradient.
    const lineGeojson: Record<string, unknown> = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: rw.line },
        properties: {},
    };

    // line-progress is 0..1 along *distance*, so the stops have to be placed by distance
    // too - sampleProgress() does that by locating each sample's vertex on the polyline.
    // gradientStops() then subdivides each span so the blend actually travels through the
    // Spectral ramp, and hard-edges any stretch we have no data for.
    const stops = gradientStops(sampleProgress(rw.line, rw.samples, rw.totalSeconds), scores.value);
    const gradient: ExpressionSpecification = ["interpolate", ["linear"], ["line-progress"], ...stops];
    const casing = $q.dark.isActive ? CASING_DARK : CASING_LIGHT;

    const existing = map.getSource("route-source");
    if (existing instanceof GeoJSONSource) {
        await existing.setData(lineGeojson);
    } else {
        map.addSource("route-source", { type: "geojson", data: lineGeojson, lineMetrics: true });
        // Slip the route underneath the basemap's labels so place names stay readable.
        const firstSymbolId = map.getStyle().layers.find(l => l.type === "symbol")?.id;
        map.addLayer(
            {
                id: "route-line-casing",
                type: "line",
                source: "route-source",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-width": 10, "line-color": casing },
            },
            firstSymbolId,
        );
        map.addLayer(
            {
                id: "route-line",
                type: "line",
                source: "route-source",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-width": 6 },
            },
            firstSymbolId,
        );
    }
    // Both outside the addSource branch: that only runs on the first render and after a
    // style swap, but the gradient and the casing change whenever the data or theme does.
    map.setPaintProperty("route-line", "line-gradient", gradient);
    map.setPaintProperty("route-line-casing", "line-color", casing);

    // 2) Weather chips + wind arrows at each sample.
    clearSampleMarkers();
    rw.samples.forEach((s, index) => {
        const rq = rideScore(s);
        const popup = new Popup({ offset: 16, closeButton: false }).setHTML(
            `<div style="font:13px/1.4 sans-serif;min-width:160px">
                <b>${fmtTime(s.eta)} Uhr</b> · ${s.weatherDesc || ""}<br>
                🌧️ ${s.rainRateMmH == null ? "—" : s.rainRateMmH.toFixed(1)} mm/h &nbsp; 🌡️ ${s.temp.toFixed(0)}°C<br>
                Regenrisiko: ${s.pop == null ? "Nicht verfügbar" : `${Math.round(s.pop * 100)}%`}<br>
                💨 ${s.windSpeed.toFixed(0)} km/h${s.windGust ? ` (Böen ${s.windGust.toFixed(0)})` : ""}<br>
                <span class="${s.headwind > 8 ? "wx-strong" : ""}">↳ ${windText(s)}</span><br>
                <span class="wx-quality">
                    <i style="background:${scoreColor(rq?.score ?? null)}"></i> Fahrqualität: ${rideScoreLabel(rq)}
                </span>
            </div>`,
        );
        const marker = new Marker({ element: sampleMarkerEl(s), anchor: "bottom" })
            .setLngLat([s.lon, s.lat])
            .setPopup(popup)
            .addTo(map);
        const el = marker.getElement();
        el.tabIndex = 0;
        el.setAttribute("role", "button");
        el.setAttribute("aria-label", `Wetter um ${fmtTime(s.eta)} Uhr auswählen`);
        el.addEventListener("click", () => { emit("selectSample", index); });
        el.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                emit("selectSample", index);
            }
        });
        el.addEventListener("mouseenter", () => marker.togglePopup());
        el.addEventListener("mouseleave", () => marker.togglePopup());
        sampleMarkers.push({ marker, popup, sample: s });
    });

    // 3) Fit the map to the route, then thin the chips for the zoom we actually ended up at
    // (fitBounds runs after the markers exist, and may not emit a zoomend on a re-render).
    const bounds = new LngLatBounds();
    rw.line.forEach(c => bounds.extend([c[0] ?? 0, c[1] ?? 0]));
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60 });
    map.once("idle", applyMarkerThinning);
    highlightSample();
}

watch(
    [routeWeather, hasMap],
    async () => {
        if (routeWeather.value && mymap.value) {
            await renderRoute(routeWeather.value);
        }
    },
    { immediate: true },
);

// setStyle() replaces the whole style, which wipes our custom source/layers (but not the
// DOM-based markers/popups, those survive) - re-add the route once the new style is ready.
watch(
    () => $q.dark.isActive,
    dark => {
        const map = mymap.value;
        if (!map) return;
        map.setStyle(dark ? DARK_STYLE : LIGHT_STYLE);
        map.once("style.load", () => {
            if (routeWeather.value) void renderRoute(routeWeather.value);
        });
    },
);

onMounted(() => {
    if (!mapContainer.value) return;
    try {
        const map = new MapLibreMap({
            container: mapContainer.value,
            style: $q.dark.isActive ? DARK_STYLE : LIGHT_STYLE,
            center: [9.252317, 47.521889],
            zoom: 12,
        });

        map.on("load", () => {
            mymap.value = map;
            emitMapView(map);
            map.on("moveend", () => {
                emitMapView(map);
            });
            map.on("zoomend", applyMarkerThinning);
        });
        map.on("error", e => {
            console.error("MapLibre error:", e);
        });
    } catch (e) {
        webglError.value =
            "WebGL konnte nicht initialisiert werden. Bitte aktiviere Hardwarebeschleunigung im Browser.";
        console.error("Map init failed:", e);
    }
});

onBeforeUnmount(() => {
    clearSampleMarkers();
    startMarker?.remove();
    destMarker?.remove();
    selectedMarker?.remove();
    mymap.value?.remove();
    mymap.value = undefined;
});
</script>

<template>
    <div class="fit flex justify-center">
        <slot name="search"></slot>
        <div v-if="webglError" class="fit flex column items-center justify-center text-center q-pa-xl">
            <div class="text-h6 q-mb-md">Karte konnte nicht geladen werden</div>
            <div class="text-body2">{{ webglError }}</div>
        </div>
        <div v-else class="wx-map-wrap" :style="{ height: height ?? '100vh' }">
            <div id="map" ref="map"></div>
            <MapLegend v-if="hasRoute" :show-no-data="hasMissingScores" class="wx-legend-anchor" />
        </div>
    </div>
</template>

<style scoped>
.wx-map-wrap {
    position: relative;
    width: 100%;
    /* Lets MapLegend shrink itself on the small saved-route tile. */
    container-type: inline-size;
}

#map {
    width: 100%;
    height: 100%;
}

/* Bottom-left: maplibre's attribution owns the bottom-right corner. */
.wx-legend-anchor {
    position: absolute;
    left: 8px;
    bottom: 8px;
    z-index: 2;
}
.overlay {
    top: 0;
    left: 0;
}
</style>

<style>
/* Marker elements are created with document.createElement and appended by maplibre, so they
   never carry the scoped-style data attribute - these rules must stay unscoped. */
.maplibregl-popup-content {
    padding: 8px 10px;
}

.wx-strong {
    color: var(--q-negative);
}

.wx-quality i {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 2px;
    vertical-align: baseline;
}

/* Popup text inherits the body color, which Quasar makes white in dark mode - so the popup
   (and its pointer tip, drawn with borders) switches to Quasar's dark surface there too. */
body.body--dark .maplibregl-popup-content {
    background: var(--q-dark);
    color: #fff;
}
body.body--dark .maplibregl-popup-anchor-top .maplibregl-popup-tip,
body.body--dark .maplibregl-popup-anchor-top-left .maplibregl-popup-tip,
body.body--dark .maplibregl-popup-anchor-top-right .maplibregl-popup-tip {
    border-bottom-color: var(--q-dark);
}
body.body--dark .maplibregl-popup-anchor-bottom .maplibregl-popup-tip,
body.body--dark .maplibregl-popup-anchor-bottom-left .maplibregl-popup-tip,
body.body--dark .maplibregl-popup-anchor-bottom-right .maplibregl-popup-tip {
    border-top-color: var(--q-dark);
}
body.body--dark .maplibregl-popup-anchor-left .maplibregl-popup-tip {
    border-right-color: var(--q-dark);
}
body.body--dark .maplibregl-popup-anchor-right .maplibregl-popup-tip {
    border-left-color: var(--q-dark);
}

.wx-marker {
    display: flex;
    align-items: center;
    gap: 2px;
    cursor: pointer;
}

.wx-selected {
    width: 24px;
    height: 24px;
    border: 3px solid #2b6cb0;
    border-radius: 50%;
    background: rgb(43 108 176 / 15%);
    box-shadow: 0 0 0 3px white;
    pointer-events: none;
}

.wx-marker--hidden {
    display: none;
}

.wx-wind {
    display: block;
    flex: none;
}

.wx-chip {
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 1px 7px 1px 3px;
    border: 2px solid #90a4ae;
    border-radius: 999px;
    background: #fff;
    box-shadow: 0 1px 4px rgb(0 0 0 / 25%);
    font:
        600 12px/1 system-ui,
        sans-serif;
    color: #2c3e50;
    white-space: nowrap;
}

.wx-chip svg {
    display: block;
    flex: none;
}
</style>
