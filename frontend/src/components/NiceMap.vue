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
import type { PlacesSearchResult, RouteForecastOut, ForecastSampleOut, WindArrow } from "@norain/api/models";
import { isNightEta, pickVisibleSamples, weatherIconSvg } from "@/utils/weatherIcons";
import { swissTime } from "@/utils/forecastDetails";
import { gradientStops, rideScore, rideScoreLabel, sampleProgress, scoreBand, scoreColor } from "@/utils/rideQuality";
import MapLegend from "@/components/MapLegend.vue";
import { useForecastMapDetail, type LineDetail } from "@/queries/forecastParts";
import { finerDetail, lineDetailForZoom } from "@/utils/mapDetail";
import { groundArrowBearing, groundWindText, visibleWindArrows, windArrowSize, windPowerText } from "@/utils/wind";

maplibreConfig.WORKER_URL = maplibreWorkerUrl;

const $q = useQuasar();

// Pale, low-ink vector basemaps (CARTO, no API key) so the route line and the weather chips
// carry the map instead of competing with OSM's POIs and landuse fills. Both styles ship
// their own OSM/CARTO attribution.
const LIGHT_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";
const DARK_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

const props = defineProps<{
    routeWeather: RouteForecastOut | undefined;
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

// --- map detail: the job result carries a coarse line and wind arrows ~2 km apart; finer
// ones are fetched once the map is zoomed in far enough to show the difference. ---
const zoomDetail = ref<LineDetail>("coarse");
/** The finest detail fetched so far for the current forecast. Zooming out keeps it. */
const loadedDetail = ref<{ detail: LineDetail; line: number[][]; windArrows: WindArrow[] } | undefined>(undefined);
const requestedDetail = computed(() => finerDetail(zoomDetail.value, loadedDetail.value?.detail ?? "coarse"));
const { data: fetchedDetail } = useForecastMapDetail(
    () => routeWeather.value?.jobId,
    () => routeWeather.value?.version,
    requestedDetail,
);
const drawnLine = computed(() => loadedDetail.value?.line ?? routeWeather.value?.line ?? []);
const drawnWindArrows = computed(() => loadedDetail.value?.windArrows ?? routeWeather.value?.windArrows ?? []);

watch(
    () => [routeWeather.value?.jobId, routeWeather.value?.version],
    () => {
        loadedDetail.value = undefined;
    },
);
watch(fetchedDetail, fetched => {
    const detail = requestedDetail.value;
    if (!fetched || detail === "coarse" || finerDetail(detail, loadedDetail.value?.detail ?? "coarse") !== detail) {
        return;
    }
    loadedDetail.value = { detail, line: fetched.line, windArrows: fetched.windArrows };
    void renderLine();
    renderWindMarkers();
});

const scores = computed(() => (routeWeather.value?.samples ?? []).map(s => rideScore(s)?.score ?? null));
const hasRoute = computed(() => (routeWeather.value?.samples.length ?? 0) > 0);
/** Only claim a "Keine Daten" swatch when a stretch really is unknown. */
const hasMissingScores = computed(() => scores.value.some(v => v === null));
const hasWindProfile = computed(() => routeWeather.value?.summary.windDistribution != null);

// --- sample marker: weather chip (condition glyph + temperature) with the wind arrow
// attached beside it. The arrow points in the direction the wind blows TOWARD. ---
function sampleMarkerEl(sample: ForecastSampleOut): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "wx-marker";
    const strong = sample.headwind != null && sample.headwind > 8;
    const glyph = weatherIconSvg(sample.weatherCode, {
        rainMm: sample.rainMm,
        night: isNightEta(sample.eta),
    });
    // wind_dir is the direction the wind comes FROM; blowing-toward = +180°. Only the arrow
    // rotates - rotating the whole marker would tip the temperature text over with it.
    el.innerHTML = `
        ${!hasWindProfile.value && sample.windDir != null && sample.windSpeed != null && sample.windSpeed > 0 ? `<svg class="wx-wind" width="16" height="16" viewBox="0 0 24 24" aria-label="Wind über Grund"
             style="transform:rotate(${sample.windDir + 180}deg)">
            <path d="M12 2 L17 13 L12 10.5 L7 13 Z"
                  fill="${strong ? "#d24d78" : "#2c3e50"}" stroke="white" stroke-width="1.5"/>
        </svg>` : ""}
        <div class="wx-chip" style="border-color:${scoreColor(rideScore(sample)?.score ?? null)}">
            <svg width="20" height="20" viewBox="0 0 24 24">${glyph}</svg>
            <span>${Math.round(sample.temp)}°</span>
        </div>`;
    return el;
}

/** A shown chip. The popup is built on first hover, since most chips are never hovered. */
interface SampleMarker { marker: Marker; popup?: Popup }
/** Markers exist only for the chips currently shown, keyed by sample index. */
let sampleMarkers = new Map<number, SampleMarker>();
/** A shown wind arrow. Its popup is built on first open. */
interface WindMarker { marker: Marker; popup?: Popup }
/** Markers exist only for the arrows currently shown, keyed by position. */
let windMarkers = new Map<string, WindMarker>();

function clearWindMarkers() {
    windMarkers.forEach(({ marker, popup }) => { popup?.remove(); marker.remove(); });
    windMarkers = new Map();
}

function windArrowLabel(arrow: WindArrow): string {
    return `Wind: ${groundWindText(arrow)} · ${windPowerText(arrow.windPowerW)}`;
}

/** The real wind, pointing where it blows; the bigger the arrow, the more it costs to hold the planned speed. */
function createWindMarker(map: MapLibreMap, arrow: WindArrow): WindMarker {
    const element = document.createElement("div");
    element.className = "wx-wind-arrow";
    element.tabIndex = 0;
    element.setAttribute("role", "button");
    element.setAttribute("aria-label", windArrowLabel(arrow));
    const size = windArrowSize(arrow.windPowerW);
    element.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 L20 17 L12 13 L4 17 Z" fill="#2f7fd8" stroke="white" stroke-width="1.5"/></svg>`;
    const marker = new Marker({ element, rotation: groundArrowBearing(arrow) ?? 0,
        rotationAlignment: "map", pitchAlignment: "map" })
        .setLngLat([arrow.lon, arrow.lat])
        .addTo(map);
    const entry: WindMarker = { marker };
    const toggle = () => {
        if (entry.popup?.isOpen()) {
            entry.popup.remove();
            return;
        }
        entry.popup ??= new Popup({ offset: 14 }).setLngLat([arrow.lon, arrow.lat]).setText(windArrowLabel(arrow));
        entry.popup.addTo(map);
    };
    element.addEventListener("click", event => {
        // Keep the map's own click handling from closing the popup straight away.
        event.stopPropagation();
        toggle();
    });
    element.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
    });
    return entry;
}

/**
 * Show the arrows that fit on screen. Arrows that stay visible keep their marker (and any
 * open popup) across a pan or zoom; only those that appear or drop out are touched.
 */
function renderWindMarkers() {
    const map = mymap.value;
    if (!map) return;
    const { clientWidth: width, clientHeight: height } = map.getCanvas();
    const arrows = visibleWindArrows(drawnWindArrows.value, arrow => {
        const point = map.project([arrow.lon, arrow.lat]);
        return point.x < 0 || point.y < 0 || point.x > width || point.y > height ? { x: NaN, y: NaN } : point;
    });
    const wanted = new Map(arrows.map(arrow => [`${arrow.lon},${arrow.lat}`, arrow]));
    for (const [key, { marker, popup }] of windMarkers) {
        if (!wanted.has(key)) {
            popup?.remove();
            marker.remove();
            windMarkers.delete(key);
        }
    }
    for (const [key, arrow] of wanted) {
        if (!windMarkers.has(key)) windMarkers.set(key, createWindMarker(map, arrow));
    }
}

function removeSampleMarker({ marker, popup }: SampleMarker) {
    popup?.remove();
    marker.remove();
}

function clearSampleMarkers() {
    sampleMarkers.forEach(removeSampleMarker);
    sampleMarkers = new Map();
}

/** Screen margin around the viewport in which chips are still built, so a short pan does
 *  not show them popping in at the edge. */
const MARKER_VIEW_MARGIN_PX = 80;

/**
 * Show the chips that fit at the current zoom and lie in view, and only build those.
 *
 * Thinning runs over every sample so the choice does not change as the map pans; the
 * viewport filter comes after it. A chip that drops out is removed from the DOM rather
 * than hidden - a zoomed-out long route would otherwise carry a marker per sample.
 */
function applyMarkerThinning() {
    const map = mymap.value;
    const samples = routeWeather.value?.samples ?? [];
    if (!map) return;
    // Carry the quality band in, so a stretch that turns bad through wind or cold keeps a
    // chip at the transition - the line's colour is never the only cue.
    const thinnable = samples.map((s, i) => ({ rainMm: s.rainMm, band: scoreBand(scores.value[i] ?? null) }));
    const points = samples.map(s => map.project([s.lon, s.lat]));
    const { clientWidth: width, clientHeight: height } = map.getCanvas();
    const inView = (i: number) => {
        const p = points[i];
        return !!p && p.x >= -MARKER_VIEW_MARGIN_PX && p.y >= -MARKER_VIEW_MARGIN_PX
            && p.x <= width + MARKER_VIEW_MARGIN_PX && p.y <= height + MARKER_VIEW_MARGIN_PX;
    };
    const visible = new Set(
        [...pickVisibleSamples(thinnable, i => points[i] ?? { x: NaN, y: NaN })].filter(inView),
    );

    for (const [index, entry] of sampleMarkers) {
        if (!visible.has(index)) {
            removeSampleMarker(entry);
            sampleMarkers.delete(index);
        }
    }
    for (const index of visible) {
        const sample = samples[index];
        if (sample && !sampleMarkers.has(index)) sampleMarkers.set(index, createSampleMarker(map, sample, index));
    }
}

function fmtTime(iso: string): string {
    return swissTime(iso);
}

function windText(s: ForecastSampleOut): string {
    if (s.headwind == null) return "Windrichtung zur Strecke nicht verfügbar";
    if (s.headwind > 1) return `${Math.round(s.headwind)} km/h Gegenwind`;
    if (s.headwind < -1) return `${Math.round(-s.headwind)} km/h Rückenwind`;
    return s.crosswind == null ? "Seitenwind nicht verfügbar" : `${Math.round(s.crosswind)} km/h Seitenwind (Abschnittsmittel)`;
}

function samplePopupHtml(s: ForecastSampleOut): string {
    const rq = rideScore(s);
    return `<div style="font:13px/1.4 sans-serif;min-width:160px">
        <b>${fmtTime(s.eta)} Uhr</b> · ${s.weatherDesc || ""}<br>
        🌧️ ${s.rainRateMmH == null ? "—" : s.rainRateMmH.toFixed(1)} mm/h &nbsp; 🌡️ ${s.temp.toFixed(0)}°C<br>
        Regenrisiko: ${s.pop == null ? "Nicht verfügbar" : `${Math.round(s.pop * 100)}%`}<br>
        💨 ${s.windSpeed == null ? "Nicht verfügbar" : `${s.windSpeed.toFixed(0)} km/h über Grund`}${s.windGust ? ` (Böen ${s.windGust.toFixed(0)})` : ""}<br>
        <span class="${s.headwind != null && s.headwind > 8 ? "wx-strong" : ""}">↳ ${windText(s)}</span><br>
        ${s.windPowerW != null ? `↳ ${windPowerText(s.windPowerW)}<br>` : ""}
        ${s.windCoverage != null && s.windCoverage < 1 ? `Windabdeckung im Abschnitt: ${Math.round(s.windCoverage * 100)}%<br>` : ""}
        <span class="wx-quality">
            <i style="background:${scoreColor(rq?.score ?? null)}"></i> Fahrqualität: ${rideScoreLabel(rq)}
        </span>
    </div>`;
}

function createSampleMarker(map: MapLibreMap, s: ForecastSampleOut, index: number): SampleMarker {
    const marker = new Marker({ element: sampleMarkerEl(s), anchor: "bottom" }).setLngLat([s.lon, s.lat]).addTo(map);
    const entry: SampleMarker = { marker };
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
    el.addEventListener("mouseenter", () => {
        if (!entry.popup) {
            entry.popup = new Popup({ offset: 16, closeButton: false }).setHTML(samplePopupHtml(s));
            marker.setPopup(entry.popup);
        }
        if (!entry.popup.isOpen()) marker.togglePopup();
    });
    el.addEventListener("mouseleave", () => {
        if (entry.popup?.isOpen()) marker.togglePopup();
    });
    return entry;
}

/** Draw the route line, coloured along its length by ride quality via line-gradient. */
async function renderLine() {
    const map = mymap.value;
    const rw = routeWeather.value;
    if (!map || !rw) return;
    const line = drawnLine.value;

    const lineGeojson: Record<string, unknown> = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: line },
        properties: {},
    };

    // line-progress is 0..1 along *distance*, so the stops have to be placed by distance
    // too - sampleProgress() does that by locating each sample's vertex on the polyline.
    // Every line level the backend serves keeps those vertices, so this works on whichever
    // line is drawn. gradientStops() then subdivides each span so the blend actually
    // travels through the Spectral ramp, and hard-edges any stretch we have no data for.
    const stops = gradientStops(sampleProgress(line, rw.samples, rw.totalSeconds), scores.value);
    const gradient: ExpressionSpecification = ["interpolate", ["linear"], ["line-progress"], ...stops];
    const casing = $q.dark.isActive ? CASING_DARK : CASING_LIGHT;

    const existing = map.getSource("route-source");
    if (existing instanceof GeoJSONSource) {
        await existing.setData(lineGeojson);
    } else {
        map.addSource("route-source", { type: "geojson", data: lineGeojson, lineMetrics: true });
        // Slip the route under the basemap's labels, but above every road, rail and boundary.
        // Not simply the first symbol: Positron has waterway_label early, below all the roads.
        const layers = map.getStyle().layers;
        let labelsStart = layers.length;
        while (labelsStart > 0 && layers[labelsStart - 1]?.type === "symbol") labelsStart--;
        const labelsStartId = layers[labelsStart]?.id; // undefined -> top of the stack
        map.addLayer(
            {
                id: "route-line-casing",
                type: "line",
                source: "route-source",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-width": 10, "line-color": casing },
            },
            labelsStartId,
        );
        map.addLayer(
            {
                id: "route-line",
                type: "line",
                source: "route-source",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-width": 6 },
            },
            labelsStartId,
        );
    }
    // Both outside the addSource branch: that only runs on the first render and after a
    // style swap, but the gradient and the casing change whenever the data or theme does.
    map.setPaintProperty("route-line", "line-gradient", gradient);
    map.setPaintProperty("route-line-casing", "line-color", casing);
}

async function renderRoute() {
    const map = mymap.value;
    if (!map) return;

    await renderLine();

    // Fit the map to the route, then pick the chips for the zoom we actually ended up at
    // (fitBounds may not emit a moveend on a re-render). The coarse line's bounds are the
    // full line's: simplification keeps both ends and every corner that matters at this scale.
    clearSampleMarkers();
    const bounds = new LngLatBounds();
    drawnLine.value.forEach(c => bounds.extend([c[0] ?? 0, c[1] ?? 0]));
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60 });
    map.once("idle", applyMarkerThinning);
    // A new forecast can put a different wind at the same spot, so no arrow carries over.
    clearWindMarkers();
    renderWindMarkers();
    highlightSample();
}

watch(
    [routeWeather, hasMap],
    async () => {
        if (routeWeather.value && mymap.value) {
            await renderRoute();
        } else if (!routeWeather.value) {
            clearSampleMarkers();
            clearWindMarkers();
        }
    },
    { immediate: true },
);

// setStyle() replaces the whole style, which wipes our custom source/layers (but not the
// DOM-based markers/popups, those survive) - re-add the line once the new style is ready.
watch(
    () => $q.dark.isActive,
    dark => {
        const map = mymap.value;
        if (!map) return;
        map.setStyle(dark ? DARK_STYLE : LIGHT_STYLE);
        map.once("style.load", () => {
            void renderLine();
        });
    },
);

function onZoomEnd(map: MapLibreMap) {
    zoomDetail.value = lineDetailForZoom(map.getZoom());
}

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
            onZoomEnd(map);
            // moveend covers zooms too; the chips depend on both zoom and what is in view.
            map.on("moveend", applyMarkerThinning);
            map.on("zoomend", () => {
                onZoomEnd(map);
            });
            // moveend also follows every zoom, so no separate zoomend listener is needed.
            map.on("moveend", renderWindMarkers);
            map.on("resize", renderWindMarkers);
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
    clearWindMarkers();
    mymap.value?.off("moveend", renderWindMarkers);
    mymap.value?.off("resize", renderWindMarkers);
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
            <div v-if="hasWindProfile" class="wx-wind-legend text-caption">
                <span aria-hidden="true">➤</span> Wind
                <div>Pfeile zeigen, wohin der Wind weht. Grösse = Windaufwand (geschätzt).</div>
            </div>
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
.wx-wind-legend { position: absolute; top: 8px; left: 8px; padding: 4px 8px;
    border-radius: 4px; background: var(--q-dark, #263238); color: white; max-width: calc(100% - 16px); }
.wx-wind-legend span { color: #77b7ff; }
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
.wx-wind-arrow { cursor: pointer; }

.wx-selected {
    width: 24px;
    height: 24px;
    border: 3px solid #2b6cb0;
    border-radius: 50%;
    background: rgb(43 108 176 / 15%);
    box-shadow: 0 0 0 3px white;
    pointer-events: none;
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
