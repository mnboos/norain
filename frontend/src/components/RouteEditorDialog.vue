<script setup lang="ts">
import ElevationChart from "@/components/ElevationChart.vue";
import { computed, onBeforeUnmount, ref, shallowRef, useTemplateRef, watch } from "vue";
import { useQuasar } from "quasar";
import { GeoJSONSource, LngLatBounds, Map as MapLibreMap, Marker, config as maplibreConfig } from "maplibre-gl";
import type { Feature, LineString } from "geojson";
import type { MapLayerMouseEvent, MapLayerTouchEvent, MapMouseEvent, MapTouchEvent } from "maplibre-gl";
import { ResponseError } from "@norain/api/runtime";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import lightStyleUrl from "@/assets/map-styles/positron.json?url";
import darkStyleUrl from "@/assets/map-styles/dark-matter.json?url";
import { previewRoute } from "@/queries/recurringRoutes";
import { CASING_DARK, CASING_LIGHT, CASING_OPACITY } from "@/utils/rideQuality";
import { routeCaption, toLonLat, viaInsertIndex, type LonLat } from "@/utils/routeEditing";

// See NiceMap.vue: the bundler moves maplibre's worker, so point maplibre at it.
maplibreConfig.WORKER_URL = maplibreWorkerUrl;

/**
 * Reshape a route by hand. Drag the line to add a via point, drag a via point to move it, tap
 * one to remove it. GraphHopper routes through them: the line here is a preview from
 * `/routes/preview`, asked for once per drag, and the saved route's geometry is still built
 * by the server's geometry task.
 */
const props = defineProps<{
    modelValue: boolean;
    start: LonLat;
    dest: LonLat;
    profile: string;
    viaPoints: number[][];
    originalCoordinates?: number[][];
}>();

const emit = defineEmits<{
    "update:modelValue": [value: boolean];
    apply: [viaPoints: LonLat[]];
}>();

const $q = useQuasar();

const START_COLOR = "#2b6cb0";
const DEST_COLOR = "#d24d78";
const LINE_COLOR = "#2b6cb0";
const PREVIEW_DEBOUNCE_MS = 250;
// A press on the line that moves less than this is a tap, not a drag.
const DRAG_THRESHOLD_PX = 4;

const mapContainer = useTemplateRef<HTMLDivElement>("map");
const map = shallowRef<MapLibreMap>();

const vias = ref<LonLat[]>([]);
// The last via points GraphHopper routed, restored when a drag leads nowhere.
let routedVias: LonLat[] = [];
const line = shallowRef<number[][]>([]);
const caption = ref("");
const previewSeconds = ref<number>();
const previewTimes = ref<number[] | null>();
const loading = ref(false);

let markers: Marker[] = [];
let debounce: ReturnType<typeof setTimeout> | undefined;
let inFlight: AbortController | undefined;

const changed = computed(() => JSON.stringify(vias.value) !== JSON.stringify(props.viaPoints));

// --------------------------------------------------------------------------- preview

function schedulePreview(delay = PREVIEW_DEBOUNCE_MS) {
    clearTimeout(debounce);
    // Pending from now, so "Übernehmen" cannot take via points the server has not routed.
    loading.value = true;
    debounce = setTimeout(() => void refreshPreview(), delay);
}

async function refreshPreview() {
    inFlight?.abort();
    const controller = new AbortController();
    inFlight = controller;
    loading.value = true;
    const asked = vias.value.map(toLonLat);
    try {
        const result = await previewRoute(
            { profile: props.profile, points: [props.start, ...asked, props.dest] },
            controller.signal,
        );
        line.value = result.coordinates;
        previewSeconds.value = result.timeS;
        previewTimes.value = result.vertexTimes;
        caption.value = routeCaption(result.distanceM, result.timeS);
        routedVias = asked;
    } catch (error) {
        if (controller.signal.aborted) return;
        vias.value = routedVias.map(toLonLat);
        $q.notify({ type: "negative", message: await errorMessage(error) });
    } finally {
        if (inFlight === controller) loading.value = false;
    }
}

async function errorMessage(error: unknown): Promise<string> {
    if (error instanceof ResponseError) {
        try {
            const body: unknown = await error.response.json();
            if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
                return body.detail;
            }
        } catch {
            // Not JSON: fall through to the generic message.
        }
    }
    return "Die Route konnte nicht berechnet werden.";
}

// --------------------------------------------------------------------------- map layers

function lineData(): Feature<LineString> {
    return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: line.value } };
}

function addLayers(m: MapLibreMap) {
    if (m.getSource("edit-line")) return;
    if (props.originalCoordinates?.length) {
        m.addSource("original-line", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: props.originalCoordinates } } });
        m.addLayer({ id: "original-line", type: "line", source: "original-line", paint: { "line-color": "#a25219", "line-width": 8, "line-opacity": 0.6 } });
    }
    m.addSource("edit-line", { type: "geojson", data: lineData() });
    m.addLayer({
        id: "edit-casing",
        type: "line",
        source: "edit-line",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: {
            "line-color": $q.dark.isActive ? CASING_DARK : CASING_LIGHT,
            "line-opacity": CASING_OPACITY,
            "line-width": 8,
        },
    });
    m.addLayer({
        id: "edit-line",
        type: "line",
        source: "edit-line",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": LINE_COLOR, "line-width": 5 },
    });
    // Wide and invisible, so the line is easy to grab with a finger.
    m.addLayer({
        id: "edit-hit",
        type: "line",
        source: "edit-line",
        paint: { "line-color": "#000", "line-opacity": 0, "line-width": 24 },
    });
}

watch(line, () => {
    void map.value?.getSource<GeoJSONSource>("edit-line")?.setData(lineData());
});

watch(loading, dim => {
    map.value?.setPaintProperty("edit-line", "line-opacity", dim ? 0.4 : 1);
});

watch(
    () => $q.dark.isActive,
    dark => {
        const m = map.value;
        if (!m) return;
        m.setStyle(dark ? darkStyleUrl : lightStyleUrl);
        m.once("style.load", () => {
            addLayers(m);
        });
    },
);

// --------------------------------------------------------------------------- markers

function dot(color: string, size: number): HTMLDivElement {
    const element = document.createElement("div");
    Object.assign(element.style, {
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: "50%",
        background: color,
        border: "2px solid white",
        boxShadow: "0 0 0 1px rgba(0, 0, 0, 0.4)",
        cursor: "grab",
    });
    return element;
}

function renderMarkers() {
    const m = map.value;
    for (const marker of markers) marker.remove();
    markers = [];
    if (!m) return;
    markers.push(new Marker({ color: START_COLOR }).setLngLat(props.start).addTo(m));
    markers.push(new Marker({ color: DEST_COLOR }).setLngLat(props.dest).addTo(m));
    vias.value.forEach((via, index) => {
        const element = dot("white", 16);
        element.style.border = `3px solid ${LINE_COLOR}`;
        element.setAttribute("role", "button");
        element.setAttribute("aria-label", `Zwischenpunkt ${index + 1} entfernen`);
        element.title = "Ziehen zum Verschieben, tippen zum Entfernen";
        let dragged = false;
        const marker = new Marker({ element, draggable: true }).setLngLat(via).addTo(m);
        marker.on("dragstart", () => {
            dragged = true;
        });
        marker.on("dragend", () => {
            const { lng, lat } = marker.getLngLat();
            vias.value = vias.value.map((v, i) => (i === index ? [lng, lat] : v));
            schedulePreview();
            // The click that ends a drag must not also remove the point.
            setTimeout(() => {
                dragged = false;
            });
        });
        element.addEventListener("click", event => {
            event.stopPropagation();
            if (dragged) return;
            vias.value = vias.value.filter((_, i) => i !== index);
            schedulePreview(0);
        });
        markers.push(marker);
    });
}

watch(vias, renderMarkers);

// --------------------------------------------------------------------------- drag the line

function beginLineDrag(m: MapLibreMap, event: MapLayerMouseEvent | MapLayerTouchEvent) {
    if (vias.value.length >= 15) return;
    if ("points" in event && event.points.length !== 1) return;
    event.preventDefault(); // keep the map from panning
    const origin = event.point;
    const ghost = new Marker({ element: dot(LINE_COLOR, 14) }).setLngLat(event.lngLat).addTo(m);
    const move = (e: MapMouseEvent | MapTouchEvent) => {
        ghost.setLngLat(e.lngLat);
    };
    const end = (e: MapMouseEvent | MapTouchEvent) => {
        m.off("mousemove", move).off("touchmove", move);
        m.off("mouseup", end).off("touchend", end);
        ghost.remove();
        if (e.point.dist(origin) < DRAG_THRESHOLD_PX) return;
        const { lng, lat } = ghost.getLngLat();
        const index = viaInsertIndex(line.value, vias.value, [event.lngLat.lng, event.lngLat.lat]);
        vias.value = [...vias.value.slice(0, index), [lng, lat], ...vias.value.slice(index)];
        schedulePreview(0);
    };
    m.on("mousemove", move);
    m.on("touchmove", move);
    m.on("mouseup", end);
    m.on("touchend", end);
}

// --------------------------------------------------------------------------- lifecycle

function onShow() {
    vias.value = props.viaPoints.map(toLonLat);
    routedVias = vias.value;
    previewSeconds.value = undefined;
    line.value = [props.start, ...vias.value, props.dest];
    caption.value = "";
    if (!mapContainer.value) return;
    const m = new MapLibreMap({
        container: mapContainer.value,
        style: $q.dark.isActive ? darkStyleUrl : lightStyleUrl,
        center: props.start,
        zoom: 12,
        attributionControl: { compact: true },
    });
    m.on("load", () => {
        map.value = m;
        addLayers(m);
        const bounds = new LngLatBounds();
        for (const point of [props.start, ...vias.value, props.dest]) bounds.extend(point);
        m.fitBounds(bounds, { padding: 48, duration: 0 });
        m.on("mousedown", "edit-hit", event => {
            beginLineDrag(m, event);
        });
        m.on("touchstart", "edit-hit", event => {
            beginLineDrag(m, event);
        });
        m.on("mouseenter", "edit-hit", () => {
            m.getCanvas().style.cursor = "grab";
        });
        m.on("mouseleave", "edit-hit", () => {
            m.getCanvas().style.cursor = "";
        });
        renderMarkers();
        void refreshPreview();
    });
}

function teardown() {
    clearTimeout(debounce);
    inFlight?.abort();
    for (const marker of markers) marker.remove();
    markers = [];
    map.value?.remove();
    map.value = undefined;
    loading.value = false;
}

onBeforeUnmount(teardown);

function reset() {
    vias.value = [];
    schedulePreview(0);
}

function apply() {
    emit("apply", vias.value);
    emit("update:modelValue", false);
}
</script>

<template>
    <q-dialog
        :model-value="modelValue"
        :maximized="$q.screen.xs"
        @update:model-value="emit('update:modelValue', $event)"
        @show="onShow"
        @hide="teardown"
    >
        <q-card style="width: 900px; max-width: 96vw" class="column no-wrap">
            <q-card-section class="q-pb-sm">
                <div class="text-h6">Strecke anpassen</div>
                <div class="text-caption">
                    Linie ziehen, um einen Zwischenpunkt zu setzen. Zwischenpunkte lassen sich verschieben oder durch
                    Tippen entfernen.
                </div>
            </q-card-section>
            <div ref="map" class="col" :style="{ minHeight: $q.screen.xs ? '0' : 'min(60dvh, 560px)' }" />
            <q-expansion-item v-if="previewSeconds && !loading" label="Höhenprofil">
                <ElevationChart :coordinates="line" :total-seconds="previewSeconds" :vertex-times="previewTimes" />
            </q-expansion-item>
            <q-card-actions>
                <div class="text-caption q-ml-sm" aria-live="polite">
                    <q-spinner v-if="loading" size="1em" class="q-mr-xs" />
                    {{ caption }}
                    <template v-if="vias.length"> · {{ vias.length }} Zwischenpunkt{{ vias.length === 1 ? "" : "e" }}</template>
                </div>
                <q-space />
                <q-btn flat no-caps label="Zurücksetzen" :disable="!vias.length" @click="reset" />
                <q-btn v-close-popup flat no-caps label="Abbrechen" />
                <q-btn color="primary" no-caps label="Übernehmen" :disable="loading || !changed" @click="apply" />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
