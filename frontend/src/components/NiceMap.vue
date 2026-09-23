<script setup lang="ts">
import { Map as MapLibreMap, Marker, Popup, LngLatBounds, GeoJSONSource, config as maplibreConfig } from "maplibre-gl";
import type { ExpressionSpecification, MapMouseEvent } from "maplibre-gl";
// maplibre-gl 6 is pure ESM and loads its worker as a sibling file resolved from
// `import.meta.url`. Once the bundler inlines maplibre into a chunk that path no longer
// exists, the worker dies silently and anything the worker parses (our GeoJSON route)
// never loads - raster tiles still paint, so it looks like the route just disappeared.
// Point maplibre at the worker the bundler emits for us instead.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { shallowRef, computed, onBeforeUnmount, onMounted, type Ref, ref, useTemplateRef, watch, toRefs } from "vue";
import { useQuasar } from "quasar";

import "maplibre-gl/dist/maplibre-gl.css";
import lightStyleUrl from "@/assets/map-styles/positron.json?url";
import darkStyleUrl from "@/assets/map-styles/dark-matter.json?url";
import type { PlacesSearchResult, RouteForecastOut, ForecastSampleOut, WindArrow } from "@norain/api/models";
import { FROST_MARK, isNightEta, pickVisibleSamples, weatherIconSvg } from "@/utils/weatherIcons";
import { swissTime } from "@/utils/forecastDetails";
import {
    alternativeColor,
    CASING_DARK,
    CASING_LIGHT,
    CASING_OPACITY,
    gradientStops,
    sampleProgress,
    scoreBand,
    scoreColor,
} from "@/utils/rideQuality";
import MapLegend from "@/components/MapLegend.vue";
import { useForecastMapDetail, type LineDetail } from "@/queries/forecastParts";
import { finerDetail, lineDetailForZoom } from "@/utils/mapDetail";
import { groundArrowBearing, groundWindText, visibleWindArrows, windArrowSize, windPowerText } from "@/utils/wind";
import { lineProgress, sampleAtRoutePoint, type ScreenPoint } from "@/utils/forecastSelection";
import { buildWindField } from "@/utils/windField";
import { WindParticleLayer } from "@/map/windParticles";
import { CANDIDATE_COLOR, poiCategory, poiName, type MapPoi } from "@/utils/poiCategories";

maplibreConfig.WORKER_URL = maplibreWorkerUrl;

const $q = useQuasar();

// Pale, low-ink vector basemaps (CARTO, no API key) so the route line and the weather chips
// carry the map instead of competing with OSM's POIs and landuse fills. Both styles ship
// their own OSM/CARTO attribution. The style files are copies of
// https://basemaps.cartocdn.com/gl/{positron,dark-matter}-gl-style/style.json, built into the
// app; the tiles, sprite and fonts they point to still come from CARTO.
const LIGHT_STYLE = lightStyleUrl;
const DARK_STYLE = darkStyleUrl;

const props = defineProps<{
    routeWeather: RouteForecastOut | undefined;
    previewLine?: number[][];
    abfahrtsort?: PlacesSearchResult;
    zielort?: PlacesSearchResult;
    /** CSS height of the map canvas. Defaults to the full viewport. */
    height?: string;
    selectedSample?: number;
    pickLocation?: boolean;
    /** Journey POIs: breaks, lodging, what is on the way. None by default. */
    pois?: MapPoi[];
    /**
     * Other routes the user can pick instead (journey alternatives), drawn thinner below the
     * route, each in `alternativeColor(index)` so the map matches the list. Their own forecast's
     * `windArrows` put them in the wind animation too.
     */
    alternativeLines?: { id: string; line: number[][]; index: number; windArrows?: WindArrow[] }[];
}>();

const { routeWeather, abfahrtsort, zielort } = toRefs(props);

const emit = defineEmits<{
    mapView: [view: { zoom: number; lat: number; lng: number }];
    selectSample: [index: number];
    selectLocation: [point: { lng: number; lat: number }];
    selectAlternative: [id: string];
}>();

const mapContainer = useTemplateRef<HTMLDivElement>("map");
const mymap: Ref<MapLibreMap | undefined> = shallowRef(undefined);
const webglError = ref<string | undefined>(undefined);

let mapInstance: MapLibreMap | undefined;
let resizeObserver: ResizeObserver | undefined;

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

// The route line is colored by ride quality (see utils/rideQuality.ts), over a 1 px casing
// that keeps the pale good end visible against CARTO Positron (CASING_* explains why).
const ROUTE_LINE_WIDTH = 6;
const CASING_WIDTH = ROUTE_LINE_WIDTH + 2;

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
const drawnLine = computed(() => loadedDetail.value?.line ?? routeWeather.value?.line ?? props.previewLine ?? []);
const drawnWindArrows = computed(() => loadedDetail.value?.windArrows ?? routeWeather.value?.windArrows ?? []);

const routeProgress = computed(() => lineProgress(drawnLine.value));
const routeSampleProgress = computed(() =>
    sampleProgress(drawnLine.value, routeWeather.value?.samples ?? [], routeWeather.value?.totalSeconds ?? 0),
);
let projectedLine: ScreenPoint[] | undefined;
let hoverFrame: number | undefined;
let hoverPoint: ScreenPoint | undefined;
function invalidateProjection() {
    projectedLine = undefined;
}
watch(drawnLine, invalidateProjection);
function selectRoutePoint(point: ScreenPoint) {
    const map = mymap.value;
    if (!map || !routeWeather.value?.samples.length) return;
    projectedLine ??= drawnLine.value.map(c => map.project([c[0] ?? 0, c[1] ?? 0]));
    const index = sampleAtRoutePoint(
        point,
        projectedLine,
        routeProgress.value,
        routeSampleProgress.value,
        props.selectedSample,
    );
    if (index !== undefined && index !== props.selectedSample) emit("selectSample", index);
}
function hoverRoute(event: MapMouseEvent) {
    if (mymap.value?.isMoving()) return;
    hoverPoint = event.point;
    if (hoverFrame !== undefined) return;
    hoverFrame = requestAnimationFrame(() => {
        hoverFrame = undefined;
        if (hoverPoint) selectRoutePoint(hoverPoint);
    });
}
function leaveRoute() {
    if (hoverFrame !== undefined) cancelAnimationFrame(hoverFrame);
    hoverFrame = undefined;
    hoverPoint = undefined;
    if (mymap.value) mymap.value.getCanvas().style.cursor = "";
}

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
    void renderLine().then(renderWindParticles);
    renderWindMarkers();
});

const scores = computed(() => (routeWeather.value?.samples ?? []).map(s => s.rideScore ?? null));
const hasRoute = computed(() => (routeWeather.value?.samples.length ?? 0) > 0);
/** Only claim a "Keine Daten" swatch when a stretch really is unknown. */
const hasMissingScores = computed(() => scores.value.some(v => v === null));
const hasWindProfile = computed(() => routeWeather.value?.summary.windDistribution != null);

// --- wind display: animated particles (map/windParticles.ts) or the clickable arrows. The
// arrows are the accessible mode and the default under reduced motion; the choice is kept
// per browser. ---
type WindMode = "animation" | "arrows";
const WIND_MODE_KEY = "norain.windMode";
function initialWindMode(): WindMode {
    try {
        const stored = localStorage.getItem(WIND_MODE_KEY);
        if (stored === "animation" || stored === "arrows") return stored;
    } catch {
        // Storage blocked (private window, previews): fall through to the default.
    }
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "arrows" : "animation";
}
const windMode = ref<WindMode>(initialWindMode());
const windModeOptions = [
    { label: "Animation", value: "animation" },
    { label: "Pfeile", value: "arrows" },
];
/** Set when the particle layer could not start (no WebGL2 support for it): arrows then. */
const particlesFailed = ref(false);
const showParticles = computed(
    () => windMode.value === "animation" && hasWindProfile.value && hasRoute.value && !particlesFailed.value,
);
// The route first, so it keeps the wind where an alternative shares its road.
const windField = computed(() =>
    buildWindField([
        { arrows: drawnWindArrows.value, line: drawnLine.value },
        ...(props.alternativeLines ?? []).map(a => ({ arrows: a.windArrows ?? [], line: a.line })),
    ]),
);
const WIND_LAYER = "wind-particles";
let windLayer: WindParticleLayer | undefined;

/**
 * Add, update or remove the particle layer. It sits directly above the route line, so the
 * wind reads over the route, and below the basemap labels like the line itself. setStyle()
 * drops custom layers, so this also runs after every theme switch.
 */
function renderWindParticles() {
    const map = mymap.value;
    if (!map) return;
    const field = showParticles.value ? windField.value : undefined;
    if (!field) {
        if (map.getLayer(WIND_LAYER)) map.removeLayer(WIND_LAYER);
        return;
    }
    windLayer ??= new WindParticleLayer(WIND_LAYER);
    windLayer.setDark($q.dark.isActive);
    windLayer.setField(field);
    windLayer.setRunning(true);
    if (map.getLayer(WIND_LAYER)) return;
    try {
        // Insert before whatever follows the route line: the first basemap label, or the
        // invisible route-hit layer when the style has no labels.
        const layers = map.getStyle().layers;
        const routeLine = layers.findIndex(layer => layer.id === "route-line");
        map.addLayer(windLayer, routeLine >= 0 ? layers[routeLine + 1]?.id : undefined);
    } catch (e) {
        console.error("Wind animation unavailable:", e);
        particlesFailed.value = true;
    }
}

watch(windMode, mode => {
    try {
        localStorage.setItem(WIND_MODE_KEY, mode);
    } catch {
        // Not remembered; the toggle still works for this page.
    }
    renderWindMarkers();
    renderWindParticles();
});
watch(particlesFailed, failed => {
    if (failed) renderWindMarkers();
});

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
        ${
            !hasWindProfile.value && sample.windDir != null && sample.windSpeed != null && sample.windSpeed > 0
                ? `<svg class="wx-wind" width="16" height="16" viewBox="0 0 24 24" aria-label="Wind über Grund"
             style="transform:rotate(${sample.windDir + 180}deg)">
            <path d="M12 2 L17 13 L12 10.5 L7 13 Z"
                  fill="${strong ? "#d24d78" : "#2c3e50"}" stroke="white" stroke-width="1.5"/>
        </svg>`
                : ""
        }
        <div class="wx-chip${sample.frostLevel ? " wx-chip--frost" : ""}" style="border-color:${scoreColor(sample.rideScore)}">
            <svg width="20" height="20" viewBox="0 0 24 24">${glyph}</svg>
            <span>${Math.round(sample.temp)}°</span>
            ${
                sample.frostLevel
                    ? `<svg class="wx-frost" width="14" height="14" viewBox="0 0 24 24"
                            role="img" aria-label="Frost: ${sample.frostLevel}">${FROST_MARK}</svg>`
                    : ""
            }
        </div>`;
    return el;
}

/** A shown chip. The popup is built on first hover, since most chips are never hovered. */
interface SampleMarker {
    marker: Marker;
    popup?: Popup;
}
/** Markers exist only for the chips currently shown, keyed by sample index. */
let sampleMarkers = new Map<number, SampleMarker>();
/** A shown wind arrow. Its popup is built on first open. */
interface WindMarker {
    marker: Marker;
    popup?: Popup;
}
/** Markers exist only for the arrows currently shown, keyed by position. */
let windMarkers = new Map<string, WindMarker>();

function clearWindMarkers() {
    windMarkers.forEach(({ marker, popup }) => {
        popup?.remove();
        marker.remove();
    });
    windMarkers = new Map();
}

function windArrowLabel(arrow: WindArrow): string {
    return `Wind: ${groundWindText(arrow)} · ${windPowerText(arrow.windEffortLevel)}`;
}

/** The real wind, pointing where it blows; the bigger the arrow, the more it costs to hold the planned speed. */
function createWindMarker(map: MapLibreMap, arrow: WindArrow): WindMarker {
    const element = document.createElement("div");
    element.className = "wx-wind-arrow";
    element.tabIndex = 0;
    element.setAttribute("role", "button");
    element.setAttribute("aria-label", windArrowLabel(arrow));
    const size = windArrowSize(arrow.windEffort);
    element.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 L20 17 L12 13 L4 17 Z" fill="#2f7fd8" stroke="white" stroke-width="1.5"/></svg>`;
    const marker = new Marker({
        element,
        rotation: groundArrowBearing(arrow) ?? 0,
        rotationAlignment: "map",
        pitchAlignment: "map",
    })
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
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
        }
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
    if (showParticles.value) {
        clearWindMarkers();
        return;
    }
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
    const thinnable = samples.map((s, i) => ({
        rainMm: s.rainMm,
        band: scoreBand(scores.value[i] ?? null),
        frost: s.frostLevel,
    }));
    const points = samples.map(s => map.project([s.lon, s.lat]));
    const { clientWidth: width, clientHeight: height } = map.getCanvas();
    const inView = (i: number) => {
        const p = points[i];
        return (
            !!p &&
            p.x >= -MARKER_VIEW_MARGIN_PX &&
            p.y >= -MARKER_VIEW_MARGIN_PX &&
            p.x <= width + MARKER_VIEW_MARGIN_PX &&
            p.y <= height + MARKER_VIEW_MARGIN_PX
        );
    };
    const visible = new Set([...pickVisibleSamples(thinnable, i => points[i] ?? { x: NaN, y: NaN })].filter(inView));

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
    return s.crosswind == null
        ? "Seitenwind nicht verfügbar"
        : `${Math.round(s.crosswind)} km/h Seitenwind (Abschnittsmittel)`;
}

/** " (gefühlt 8°C)" where the wind chill at riding speed differs from the thermometer. */
function feltText(s: ForecastSampleOut): string {
    if (s.feltTemp == null || Math.round(s.feltTemp) === Math.round(s.temp)) return "";
    return ` (gefühlt ${s.feltTemp.toFixed(0)}°C)`;
}

function samplePopupHtml(s: ForecastSampleOut): string {
    return `<div style="font:13px/1.4 var(--app-font);min-width:160px">
        <b>${fmtTime(s.eta)} Uhr</b> · ${s.weatherDesc || ""}<br>
        🌧️ ${s.rainRateMmH == null ? "—" : s.rainRateMmH.toFixed(1)} mm/h &nbsp; 🌡️ ${s.temp.toFixed(0)}°C${feltText(s)}<br>
        Regenrisiko: ${s.pop == null ? "Nicht verfügbar" : `${Math.round(s.pop * 100)}%`}<br>
        💨 ${s.windSpeed == null ? "Nicht verfügbar" : `${s.windSpeed.toFixed(0)} km/h über Grund`}${s.windGust ? ` (Böen ${s.windGust.toFixed(0)})` : ""}<br>
        <span class="${s.headwind != null && s.headwind > 8 ? "wx-strong" : ""}">↳ ${windText(s)}</span><br>
        ${s.windEffortLevel != null ? `↳ ${windPowerText(s.windEffortLevel)}<br>` : ""}
        ${s.frostLevel != null ? `❄️ Frost: ${s.frostLevel}<br>` : ""}
        ${s.windCoverage != null && s.windCoverage < 1 ? "Für Teile dieses Abschnitts fehlen Winddaten.<br>" : ""}
        <span class="wx-quality">
            <i style="background:${scoreColor(s.rideScore)}"></i> Fahrqualität: ${s.rideLabel ?? "Nicht verfügbar"}
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
    el.addEventListener("click", () => {
        emit("selectSample", index);
    });
    el.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            emit("selectSample", index);
        }
    });
    el.addEventListener("mouseenter", () => {
        leaveRoute();
        emit("selectSample", index);
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
    if (!map) return;
    const line = drawnLine.value;

    const lineGeojson: GeoJSON.Feature<GeoJSON.LineString> = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: line },
        properties: {},
    };

    // line-progress is 0..1 along *distance*, so the stops have to be placed by distance
    // too - sampleProgress() does that by locating each sample's vertex on the polyline.
    // Every line level the backend serves keeps those vertices, so this works on whichever
    // line is drawn. gradientStops() then subdivides each span so the blend actually
    // travels through the ramp, and hard-edges any stretch we have no data for.
    const stops = gradientStops(sampleProgress(line, rw?.samples ?? [], rw?.totalSeconds ?? 0), scores.value);
    const gradient: ExpressionSpecification = rw ? ["interpolate", ["linear"], ["line-progress"], ...stops] : ["interpolate", ["linear"], ["line-progress"], 0, "#2563eb", 1, "#2563eb"];
    const casing = $q.dark.isActive ? CASING_DARK : CASING_LIGHT;

    const existing = map.getSource("route-source");
    if (existing instanceof GeoJSONSource) {
        await existing.setData(lineGeojson);
    } else {
        map.addSource("route-source", { type: "geojson", data: lineGeojson, lineMetrics: true });
        const labelsStartId = firstLabelLayerId(map);
        map.addLayer(
            {
                id: "route-line-casing",
                type: "line",
                source: "route-source",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-width": CASING_WIDTH, "line-color": casing, "line-opacity": CASING_OPACITY },
            },
            labelsStartId,
        );
        map.addLayer(
            {
                id: "route-line",
                type: "line",
                source: "route-source",
                layout: { "line-cap": "round", "line-join": "round" },
                paint: { "line-width": ROUTE_LINE_WIDTH },
            },
            labelsStartId,
        );
        map.addLayer({
            id: "route-hit",
            type: "line",
            source: "route-source",
            layout: { "line-cap": "round", "line-join": "round" },
            paint: { "line-width": 24, "line-opacity": 0 },
        });
    }
    // Both outside the addSource branch: that only runs on the first render and after a
    // style swap, but the gradient and the casing change whenever the data or theme does.
    map.setPaintProperty("route-line", "line-gradient", gradient);
    map.setPaintProperty("route-line-casing", "line-color", casing);
    await renderAlternatives();
}

/**
 * Slip route lines under the basemap's labels, but above every road, rail and boundary.
 * Not simply the first symbol: Positron has waterway_label early, below all the roads.
 * Undefined means the top of the stack.
 */
function firstLabelLayerId(map: MapLibreMap): string | undefined {
    const layers = map.getStyle().layers;
    let labelsStart = layers.length;
    while (labelsStart > 0 && layers[labelsStart - 1]?.type === "symbol") labelsStart--;
    return layers[labelsStart]?.id;
}

// --- alternatives: the routes the user could pick instead, one muted colour, always below the
// route itself, and a click on one selects it ---
const ALTERNATIVE_SOURCE = "route-alternatives";
const ALTERNATIVE_LAYER = "route-alternative";
const ALTERNATIVE_HIT_LAYER = "route-alternative-hit";
const ALTERNATIVE_WIDTH = 4;

async function renderAlternatives() {
    const map = mymap.value;
    if (!map) return;
    const data: GeoJSON.FeatureCollection<GeoJSON.LineString> = {
        type: "FeatureCollection",
        features: (props.alternativeLines ?? [])
            .filter(alternative => alternative.line.length > 1)
            .map(alternative => ({
                type: "Feature",
                geometry: { type: "LineString", coordinates: alternative.line },
                properties: { id: alternative.id, color: alternativeColor(alternative.index, $q.dark.isActive) },
            })),
    };
    const existing = map.getSource(ALTERNATIVE_SOURCE);
    if (existing instanceof GeoJSONSource) {
        await existing.setData(data);
        return;
    }
    if (!data.features.length) return;
    map.addSource(ALTERNATIVE_SOURCE, { type: "geojson", data });
    // Below the route's casing when it is drawn; otherwise at the label boundary, where the
    // route's layers will later be inserted above these.
    const before = map.getLayer("route-line-casing") ? "route-line-casing" : firstLabelLayerId(map);
    map.addLayer(
        {
            id: ALTERNATIVE_LAYER,
            type: "line",
            source: ALTERNATIVE_SOURCE,
            layout: { "line-cap": "round", "line-join": "round" },
            paint: { "line-width": ALTERNATIVE_WIDTH, "line-color": ["get", "color"], "line-opacity": 0.85 },
        },
        before,
    );
    map.addLayer(
        {
            id: ALTERNATIVE_HIT_LAYER,
            type: "line",
            source: ALTERNATIVE_SOURCE,
            layout: { "line-cap": "round", "line-join": "round" },
            paint: { "line-width": 16, "line-opacity": 0 },
        },
        before,
    );
}
// An alternative's wind can arrive after the route: the particle field follows.
watch([() => props.alternativeLines, hasMap], () => void renderAlternatives().then(renderWindParticles));

function selectAlternative(event: MapMouseEvent & { features?: GeoJSON.Feature[] }) {
    const map = mymap.value;
    const id: unknown = event.features?.[0]?.properties?.id;
    if (!map || typeof id !== "string") return;
    // Where an alternative runs along the chosen route, the click belongs to the route.
    if (map.getLayer("route-hit") && map.queryRenderedFeatures(event.point, { layers: ["route-hit"] }).length) return;
    emit("selectAlternative", id);
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
    // The alternatives too, so a variant that swings further out is not cut off.
    for (const line of [drawnLine.value, ...(props.alternativeLines ?? []).map(a => a.line)]) {
        line.forEach(c => bounds.extend([c[0] ?? 0, c[1] ?? 0]));
    }
    renderWindParticles();
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60 });
    // The moveend listener thins the chips once the fit lands. Not "idle": the particle loop
    // repaints every frame, so a map with the animation running never goes idle.
    if (!map.isMoving()) applyMarkerThinning();
    // A new forecast can put a different wind at the same spot, so no arrow carries over.
    clearWindMarkers();
    renderWindMarkers();
    highlightSample();
}

watch(
    [routeWeather, () => props.previewLine, hasMap],
    async () => {
        if ((routeWeather.value || props.previewLine?.length) && mymap.value) {
            await renderRoute();
        } else if (!routeWeather.value) {
            void mymap.value?.getSource<GeoJSONSource>("route-source")?.setData({ type: "FeatureCollection", features: [] });
            clearSampleMarkers();
            clearWindMarkers();
            renderWindParticles();
        }
    },
    { immediate: true },
);

// --- journey POIs: the category's emoji on a disc, and a popup with the name (and, for a
// candidate, that it is not planned) on click. Planned stops are DOM markers in the category
// colour, stacked above the weather chips (a canvas layer always sits under every DOM marker);
// the grey candidates, which can run to hundreds, stay one symbol layer and may sit under a chip ---
const POI_SOURCE = "journey-pois";
const POI_LAYER = "journey-poi";
// Badges are drawn at this multiple of their CSS size, so they stay sharp on HiDPI screens.
const POI_PIXEL_RATIO = 2;

function poiLabel(poi: MapPoi): string {
    return `${poiCategory(poi.category).emoji} ${poiName(poi)}${poi.planned ? "" : ` · ${poi.note ?? "nicht eingeplant"}`}`;
}

/** A shown planned stop. Its popup is built on first open. */
interface PoiMarker {
    marker: Marker;
    popup?: Popup;
}
let poiMarkers: PoiMarker[] = [];

function clearPoiMarkers() {
    poiMarkers.forEach(({ marker, popup }) => {
        popup?.remove();
        marker.remove();
    });
    poiMarkers = [];
}

function createPoiMarker(map: MapLibreMap, poi: MapPoi): PoiMarker {
    const element = document.createElement("div");
    element.className = "wx-poi";
    element.tabIndex = 0;
    element.setAttribute("role", "button");
    element.setAttribute("aria-label", poiLabel(poi));
    element.style.background = poiCategory(poi.category).color;
    element.textContent = poiCategory(poi.category).emoji;
    const marker = new Marker({ element }).setLngLat([poi.lon, poi.lat]).addTo(map);
    const entry: PoiMarker = { marker };
    const toggle = () => {
        if (entry.popup?.isOpen()) {
            entry.popup.remove();
            return;
        }
        entry.popup ??= new Popup({ offset: 16 }).setLngLat([poi.lon, poi.lat]).setText(poiLabel(poi));
        entry.popup.addTo(map);
    };
    element.addEventListener("click", event => {
        // Keep the map's own click handling from closing the popup straight away.
        event.stopPropagation();
        toggle();
    });
    element.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
        }
    });
    return entry;
}

/** A candidate's badge: its category's emoji on a grey disc with a white ring. The basemap's
 * glyph server has no emoji, so the badge is a canvas image, not a text label. */
function poiBadge(category: string): ImageData | undefined {
    const size = 22 * POI_PIXEL_RATIO;
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) return undefined;
    ctx.canvas.width = ctx.canvas.height = size;
    const ring = 1.5 * POI_PIXEL_RATIO;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - ring / 2, 0, 2 * Math.PI);
    ctx.fillStyle = CANDIDATE_COLOR;
    ctx.fill();
    ctx.lineWidth = ring;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    ctx.font = `${Math.round(size * 0.55)}px "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(poiCategory(category).emoji, size / 2, size / 2 + size * 0.04);
    return ctx.getImageData(0, 0, size, size);
}

function poiIcon(map: MapLibreMap, category: string): string {
    const id = `poi-candidate-${category}`;
    if (!map.hasImage(id)) {
        const badge = poiBadge(category);
        if (badge) map.addImage(id, badge, { pixelRatio: POI_PIXEL_RATIO });
    }
    return id;
}

async function renderPois() {
    const map = mymap.value;
    if (!map) return;
    const pois = props.pois ?? [];
    clearPoiMarkers();
    poiMarkers = pois.filter(poi => poi.planned).map(poi => createPoiMarker(map, poi));
    const data: GeoJSON.FeatureCollection<GeoJSON.Point> = {
        type: "FeatureCollection",
        features: pois
            .filter(poi => !poi.planned)
            .map(poi => ({
                type: "Feature",
                geometry: { type: "Point", coordinates: [poi.lon, poi.lat] },
                properties: { icon: poiIcon(map, poi.category), label: poiLabel(poi) },
            })),
    };
    const existing = map.getSource(POI_SOURCE);
    if (existing instanceof GeoJSONSource) {
        await existing.setData(data);
        return;
    }
    if (!data.features.length) return;
    map.addSource(POI_SOURCE, { type: "geojson", data });
    map.addLayer({
        id: POI_LAYER,
        type: "symbol",
        source: POI_SOURCE,
        layout: {
            "icon-image": ["get", "icon"],
            // Every stop stays visible: a hidden break would read as "nothing here".
            "icon-allow-overlap": true,
            "icon-ignore-placement": true,
            // Smaller when zoomed out, so a day's worth of candidates does not bury the line.
            "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.6, 13, 1],
        },
        paint: { "icon-opacity": 0.85 },
    });
}
watch([() => props.pois, hasMap], () => void renderPois());

function showPoiPopup(event: MapMouseEvent & { features?: GeoJSON.Feature[] }) {
    const map = mymap.value;
    const feature = event.features?.[0];
    if (!map || feature?.geometry.type !== "Point") return;
    const [lng, lat] = feature.geometry.coordinates;
    new Popup({ offset: 16 })
        .setLngLat([lng ?? 0, lat ?? 0])
        .setText(String(feature.properties?.label ?? ""))
        .addTo(map);
}

// setStyle() replaces the whole style, which wipes our custom source/layers (but not the
// DOM-based markers/popups, those survive) - re-add the line once the new style is ready.
watch(
    () => $q.dark.isActive,
    dark => {
        const map = mymap.value;
        if (!map) return;
        map.setStyle(dark ? DARK_STYLE : LIGHT_STYLE);
        map.once("style.load", () => {
            void renderLine().then(renderWindParticles);
            void renderPois();
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
            attributionControl: {
                compact: true,

                // customAttribution: "© OpenStreetMap-Mitwirkende, © CARTO",
            },
        });

        mapInstance = map;
        resizeObserver = new ResizeObserver(() => map.resize());
        resizeObserver.observe(mapContainer.value);
        map.on("click", event => {
            if (props.pickLocation) emit("selectLocation", { lng: event.lngLat.wrap().lng, lat: event.lngLat.lat });
        });
        map.on("load", () => {
            mymap.value = map;
            const bounds = new LngLatBounds();
            for (const place of [props.abfahrtsort, props.zielort]) {
                if (place) {
                    const [lng, lat] = place.geometry.coordinates;
                    if (lng !== undefined && lat !== undefined) bounds.extend([lng, lat]);
                }
            }
            if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60, maxZoom: 13, duration: 0 });
            map.on("move", invalidateProjection);
            map.on("resize", invalidateProjection);
            map.on("movestart", leaveRoute);
            map.on("mousemove", "route-hit", hoverRoute);
            map.on("click", "route-hit", event => {
                leaveRoute();
                selectRoutePoint(event.point);
            });
            map.on("mouseenter", "route-hit", () => {
                map.getCanvas().style.cursor = "pointer";
            });
            map.on("mouseleave", "route-hit", leaveRoute);
            map.on("click", ALTERNATIVE_HIT_LAYER, selectAlternative);
            map.on("mouseenter", ALTERNATIVE_HIT_LAYER, () => {
                map.getCanvas().style.cursor = "pointer";
            });
            map.on("mouseleave", ALTERNATIVE_HIT_LAYER, () => {
                map.getCanvas().style.cursor = "";
            });
            map.on("click", POI_LAYER, showPoiPopup);
            map.on("mouseenter", POI_LAYER, () => {
                map.getCanvas().style.cursor = "pointer";
            });
            map.on("mouseleave", POI_LAYER, () => {
                map.getCanvas().style.cursor = "";
            });
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
    leaveRoute();
    clearSampleMarkers();
    clearWindMarkers();
    clearPoiMarkers();
    if (mymap.value?.getLayer(WIND_LAYER)) mymap.value.removeLayer(WIND_LAYER);
    mymap.value?.off("moveend", renderWindMarkers);
    mymap.value?.off("resize", renderWindMarkers);
    startMarker?.remove();
    destMarker?.remove();
    selectedMarker?.remove();
    resizeObserver?.disconnect();
    mapInstance?.remove();
    mymap.value = undefined;
});
</script>

<template>
    <q-card flat class="transparent column col wx-map-wrap" :style="height ? { height, flex: 'none' } : undefined">
        <slot name="search"></slot>
        <q-card-section v-if="webglError" class="fit flex column items-center justify-center text-center q-pa-xl">
            <div class="text-h6 q-mb-md">Karte konnte nicht geladen werden</div>
            <div class="text-body2">{{ webglError }}</div>
        </q-card-section>
        <q-card-section v-else class="col column q-pa-none">
            <div ref="map" class="col map-canvas"></div>
            <MapLegend v-if="hasRoute" :show-no-data="hasMissingScores" class="wx-legend-anchor" />
            <div
                v-if="hasWindProfile"
                class="wx-wind-legend text-caption"
                data-testid="wind-legend"
                :data-wind-mode="showParticles ? 'animation' : 'arrows'"
            >
                <div class="row items-center no-wrap q-gutter-x-sm">
                    <q-item-label caption>
                        <span aria-hidden="true">➤</span>
                        Wind
                    </q-item-label>
                    <q-btn-toggle
                        v-model="windMode"
                        :options="windModeOptions"
                        dense
                        no-caps
                        unelevated
                        size="sm"
                        toggle-color="primary"
                        text-color="white"
                        aria-label="Winddarstellung"
                    />
                </div>
                <div v-if="showParticles">Partikel zeigen Richtung und Stärke des Winds zur Fahrzeit</div>
                <div v-else>Pfeile zeigen Richtung und Stärke</div>
            </div>
        </q-card-section>
    </q-card>
</template>

<style scoped>
.wx-map-wrap {
    position: relative;
    width: 100%;
    /* Lets MapLegend shrink itself on the small saved-route tile. */
    container-type: inline-size;
}

.map-canvas {
    width: 100%;
    min-height: 240px;
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
.wx-wind-legend {
    position: absolute;
    top: 8px;
    left: 8px;
    padding: 4px 8px;
    border-radius: 4px;
    background: var(--q-dark, #263238);
    color: white;
    max-width: calc(100% - 16px);
}
.wx-wind-legend span {
    color: #77b7ff;
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
.wx-wind-arrow {
    cursor: pointer;
}

/* A planned stop. Every marker is a sibling in maplibre's canvas container, so the z-index
   lifts it over the weather chips; popups go above both. */
.wx-poi {
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    box-sizing: border-box;
    border: 2.5px solid #fff;
    border-radius: 50%;
    box-shadow: 0 1px 4px rgb(0 0 0 / 25%);
    font: 16px/1 "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
    cursor: pointer;
}
.maplibregl-popup {
    z-index: 2;
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
    font: 600 12px/1 var(--app-font);
    color: #2c3e50;
    white-space: nowrap;
}

.wx-chip svg {
    display: block;
    flex: none;
}

/* Frost adds a crystal and a cold tint to the chip; the weather glyph keeps saying what the
   sky is doing, so a cold clear morning still shows its sun. The border carries ride quality. */
.wx-chip--frost {
    background: #eaf4fb;
    color: #14506e;
}

.wx-frost {
    display: block;
    flex: none;
}
</style>
