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

import "maplibre-gl/dist/maplibre-gl.css";
import type { PlacesSearchResult, RouteWeatherOut, WeatherSample } from "@norain/api";
import { isNightEta, pickVisibleSamples, weatherIconSvg } from "@/utils/weatherIcons";

maplibreConfig.WORKER_URL = maplibreWorkerUrl;

const props = defineProps<{
    routeWeather: RouteWeatherOut | undefined;
    abfahrtsort?: PlacesSearchResult;
    zielort?: PlacesSearchResult;
    /** CSS height of the map canvas. Defaults to the full viewport. */
    height?: string;
}>();

const { routeWeather, abfahrtsort, zielort } = toRefs(props);

const emit = defineEmits<(e: "mapView", view: { zoom: number; lat: number; lng: number }) => void>();

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
    startMarker = placeMarker(startMarker, abfahrtsort.value, "#1565c0");
});
watch([zielort, hasMap], () => {
    destMarker = placeMarker(destMarker, zielort.value, "#2e7d32");
});

// --- rain color helper: 0mm green -> yellow -> red ---
function rainColor(mm: number): string {
    if (mm < 0.1) return "#2ecc71"; // dry
    if (mm < 0.5) return "#a3d977";
    if (mm < 1.5) return "#f1c40f";
    if (mm < 4) return "#e67e22";
    return "#e74c3c"; // heavy
}

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
                  fill="${strong ? "#c0392b" : "#2c3e50"}" stroke="white" stroke-width="1.5"/>
        </svg>
        <div class="wx-chip" style="border-color:${rainColor(sample.rainMm)}">
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
    const visible = pickVisibleSamples(samples, i => {
        const s = samples[i];
        return map.project([s?.lon ?? 0, s?.lat ?? 0]);
    });
    sampleMarkers.forEach(({ marker }, i) => {
        marker.getElement().classList.toggle("wx-marker--hidden", !visible.has(i));
    });
}

function fmtTime(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" });
}

function windText(s: WeatherSample): string {
    if (s.headwind > 1) return `${Math.round(s.headwind)} km/h Gegenwind`;
    if (s.headwind < -1) return `${Math.round(-s.headwind)} km/h Rückenwind`;
    return "Seitenwind";
}

async function renderRoute(rw: RouteWeatherOut) {
    const map = mymap.value;
    if (!map) return;

    // 1) Route line, colored along its length by rain via line-gradient.
    const lineGeojson: Record<string, unknown> = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: rw.line },
        properties: {},
    };

    // Build a line-gradient expression from the samples. line-progress is 0..1 along distance;
    // we approximate each sample's progress by its share of total ride time (good enough for color).
    const total = rw.totalSeconds || 1;
    const stops: (number | string)[] = [];
    let lastP = -1;
    rw.samples.forEach(s => {
        let p = Math.min(1, Math.max(0, s.elapsedS / total));
        if (p <= lastP) p = lastP + 0.0001; // line-gradient stops must be strictly increasing
        lastP = p;
        stops.push(p, rainColor(s.rainMm));
    });
    if (stops.length < 4) {
        stops.length = 0;
        stops.push(0, rainColor(rw.samples[0]?.rainMm ?? 0), 1, rainColor(rw.summary.maxRainMm));
    }

    const gradient: ExpressionSpecification = ["interpolate", ["linear"], ["line-progress"], ...stops];

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
                paint: { "line-width": 10, "line-color": "#ffffff" },
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
    map.setPaintProperty("route-line", "line-gradient", gradient);

    // 2) Weather chips + wind arrows at each sample.
    clearSampleMarkers();
    rw.samples.forEach(s => {
        const popup = new Popup({ offset: 16, closeButton: false }).setHTML(
            `<div style="font:13px/1.4 sans-serif;min-width:160px">
                <b>${fmtTime(s.eta)} Uhr</b> · ${s.weatherDesc || ""}<br>
                🌧️ ${s.rainMm.toFixed(1)} mm &nbsp; 🌡️ ${s.temp.toFixed(0)}°C<br>
                💨 ${s.windSpeed.toFixed(0)} km/h${s.windGust ? ` (Böen ${s.windGust.toFixed(0)})` : ""}<br>
                <span style="color:${s.headwind > 8 ? "#c0392b" : "#2c3e50"}">↳ ${windText(s)}</span>
            </div>`,
        );
        const marker = new Marker({ element: sampleMarkerEl(s), anchor: "bottom" })
            .setLngLat([s.lon, s.lat])
            .setPopup(popup)
            .addTo(map);
        const el = marker.getElement();
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

onMounted(() => {
    if (!mapContainer.value) return;
    try {
        const map = new MapLibreMap({
            // Pale, low-ink vector basemap (CARTO Positron, no API key) so the route line and
            // the weather chips carry the map instead of competing with OSM's POIs and
            // landuse fills. The style ships its own OSM/CARTO attribution.
            container: mapContainer.value,
            style: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
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
        <div v-else id="map" ref="map" :style="{ height: height ?? '100vh' }"></div>
    </div>
</template>

<style scoped>
#map {
    width: 100%;
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

.wx-marker {
    display: flex;
    align-items: center;
    gap: 2px;
    cursor: pointer;
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
