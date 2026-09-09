<script setup lang="ts">
import { Map as MapLibreMap, Marker, Popup, LngLatBounds, GeoJSONSource, config as maplibreConfig } from "maplibre-gl";
import type { ExpressionSpecification } from "maplibre-gl";
// maplibre-gl 6 is pure ESM and loads its worker as a sibling file resolved from
// `import.meta.url`. Once the bundler inlines maplibre into a chunk that path no longer
// exists, the worker dies silently and anything the worker parses (our GeoJSON route)
// never loads - raster tiles still paint, so it looks like the route just disappeared.
// Point maplibre at the worker the bundler emits for us instead.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { computed, onMounted, type Ref, ref, useTemplateRef, watch, toRefs } from "vue";

import "maplibre-gl/dist/maplibre-gl.css";
import type { PlacesSearchResult, RouteWeatherOut, WeatherSample } from "@norain/api";

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

// --- wind arrow marker element: arrow points in the direction the wind blows TOWARD ---
function windArrowEl(sample: WeatherSample): HTMLDivElement {
    const el = document.createElement("div");
    const size = Math.min(34, 14 + sample.windSpeed * 0.6);
    const strong = sample.headwind > 8;
    el.innerHTML = `
        <svg width="${size}" height="${size}" viewBox="0 0 24 24" style="display:block">
            <path d="M12 2 L17 13 L12 10.5 L7 13 Z"
                  fill="${strong ? "#c0392b" : "#2c3e50"}" stroke="white" stroke-width="1"/>
        </svg>`;
    // wind_dir is the direction the wind comes FROM; blowing-toward = +180°.
    el.style.transform = `rotate(${sample.windDir + 180}deg)`;
    el.style.cursor = "pointer";
    return el;
}

let windMarkers: { marker: Marker; popup: Popup }[] = [];

function clearWindMarkers() {
    windMarkers.forEach(({ marker, popup }) => {
        popup.remove();
        marker.remove();
    });
    windMarkers = [];
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
        map.addLayer({
            id: "route-line",
            type: "line",
            source: "route-source",
            layout: { "line-cap": "round", "line-join": "round" },
            paint: { "line-width": 6 },
        });
    }
    map.setPaintProperty("route-line", "line-gradient", gradient);

    // 2) Wind arrows + weather popups at each sample.
    clearWindMarkers();
    rw.samples.forEach(s => {
        const popup = new Popup({ offset: 16, closeButton: false }).setHTML(
            `<div style="font:13px/1.4 sans-serif;min-width:160px">
                <b>${fmtTime(s.eta)} Uhr</b> · ${s.weatherDesc || ""}<br>
                🌧️ ${s.rainMm.toFixed(1)} mm &nbsp; 🌡️ ${s.temp.toFixed(0)}°C<br>
                💨 ${s.windSpeed.toFixed(0)} km/h${s.windGust ? ` (Böen ${s.windGust.toFixed(0)})` : ""}<br>
                <span style="color:${s.headwind > 8 ? "#c0392b" : "#2c3e50"}">↳ ${windText(s)}</span>
            </div>`,
        );
        const marker = new Marker({ element: windArrowEl(s) }).setLngLat([s.lon, s.lat]).setPopup(popup).addTo(map);
        const el = marker.getElement();
        el.addEventListener("mouseenter", () => marker.togglePopup());
        el.addEventListener("mouseleave", () => marker.togglePopup());
        windMarkers.push({ marker, popup });
    });

    // 3) Fit the map to the route.
    const bounds = new LngLatBounds();
    rw.line.forEach(c => bounds.extend([c[0] ?? 0, c[1] ?? 0]));
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60 });
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
            container: mapContainer.value,
            style: {
                version: 8,
                sources: {
                    osm: {
                        type: "raster",
                        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
                        tileSize: 256,
                        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                    },
                },
                layers: [{ id: "osm", type: "raster", source: "osm" }],
            },
            center: [9.252317, 47.521889],
            zoom: 12,
        });

        map.on("load", () => {
            mymap.value = map;
            emitMapView(map);
            map.on("moveend", () => {
                emitMapView(map);
            });
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
.maplibregl-popup-content {
    padding: 8px 10px;
}
</style>
