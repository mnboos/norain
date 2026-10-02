<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { Map as MapLibreMap, NavigationControl, GeoJSONSource, config } from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import lightStyle from "@/assets/map-styles/positron.json?url";
import darkStyle from "@/assets/map-styles/dark-matter.json?url";
import type { SystemFeature, SystemCoveragePoint, SystemElevationBoxes } from "@norain/api/models";
import { systemGeoJson, coverageGeoJson, dataCoverageGeoJson, featureKey } from "@/utils/systemOverview";

const { t } = useI18n();

const props = defineProps<{
    items: SystemFeature[];
    bounds: number[] | null;
    selected: SystemFeature | null;
    points: SystemCoveragePoint[];
    coverageKind: "forecast" | "ensemble";
    now: number;
    maxAgeSeconds: number;
    roadBoxes: number[][];
    elevationBoxes?: SystemElevationBoxes;
    showRoads: boolean;
    showElevation: boolean;
    /** Where the map opens (from the URL); without it, it fits `bounds`. Read once, on mount. */
    initialView?: { center: [number, number]; zoom: number } | null;
}>();
const emit = defineEmits<{
    viewport: [value: { bbox: string; zoom: number; center: [number, number] }];
    select: [feature: SystemFeature];
    error: [message: string];
}>();
const container = ref<HTMLElement>();
const $q = useQuasar();
let map: MapLibreMap | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
config.WORKER_URL = maplibreWorkerUrl;

function reportViewport() {
    if (!map) return;
    const bounds = map.getBounds();
    emit("viewport", {
        bbox: [
            Math.max(-180, bounds.getWest()),
            Math.max(-90, bounds.getSouth()),
            Math.min(180, bounds.getEast()),
            Math.min(90, bounds.getNorth()),
        ].join(","),
        zoom: map.getZoom(),
        center: map.getCenter().toArray(),
    });
}
function dataCoverage() {
    return dataCoverageGeoJson(props.roadBoxes, props.elevationBoxes, {
        roads: props.showRoads,
        elevation: props.showElevation,
    });
}
function update() {
    if (!map) return;
    const features = map.getSource("system"),
        coverage = map.getSource("coverage"),
        area = map.getSource("data-coverage");
    if (
        !(features instanceof GeoJSONSource) ||
        !(coverage instanceof GeoJSONSource) ||
        !(area instanceof GeoJSONSource)
    )
        return;
    void features.setData(systemGeoJson(props.items, props.now, props.maxAgeSeconds));
    void coverage.setData(coverageGeoJson(props.points, props.coverageKind));
    void area.setData(dataCoverage());
    map.setFilter("selected-route", ["==", ["get", "key"], props.selected ? featureKey(props.selected) : ""]);
}
function installLayers() {
    if (!map || map.getSource("system")) return;
    map.addSource("system", { type: "geojson", data: systemGeoJson(props.items, props.now, props.maxAgeSeconds) });
    map.addSource("coverage", { type: "geojson", data: coverageGeoJson(props.points, props.coverageKind) });
    map.addSource("data-coverage", { type: "geojson", data: dataCoverage() });
    const before = map.getStyle().layers.find(layer => layer.type === "symbol")?.id;
    // Map data coverage lies under everything else: it is the background the rest is read against.
    map.addLayer(
        {
            id: "elevation-coverage",
            type: "fill",
            source: "data-coverage",
            filter: ["==", ["get", "layer"], "elevation"],
            paint: { "fill-color": ["get", "color"], "fill-opacity": 0.3, "fill-antialias": false },
        },
        before,
    );
    map.addLayer(
        {
            id: "road-coverage",
            type: "fill",
            source: "data-coverage",
            filter: ["==", ["get", "layer"], "roads"],
            // A fill, not outlines: every run of cells is its own box, and their shared edges
            // would draw a grid.
            paint: { "fill-color": ["get", "color"], "fill-opacity": 0.2, "fill-antialias": false },
        },
        before,
    );
    map.addLayer(
        {
            id: "cell-hit",
            type: "fill",
            source: "system",
            filter: ["==", ["get", "cell"], true],
            paint: { "fill-opacity": 0 },
        },
        before,
    );
    map.addLayer(
        {
            id: "cell-outlines",
            type: "line",
            source: "system",
            filter: ["==", ["get", "cell"], true],
            paint: { "line-color": ["get", "color"], "line-width": 1.5, "line-opacity": 0.85 },
        },
        before,
    );
    map.addLayer(
        {
            id: "route-lines",
            type: "line",
            source: "system",
            filter: ["==", ["get", "cell"], false],
            paint: {
                "line-color": ["get", "color"],
                "line-width": ["case", ["get", "alternative"], 1.5, 2.5],
                "line-opacity": ["case", ["any", ["get", "inactive"], ["get", "alternative"]], 0.45, 0.85],
            },
        },
        before,
    );
    map.addLayer(
        {
            id: "selected-route",
            type: "line",
            source: "system",
            filter: ["==", ["get", "key"], ""],
            paint: { "line-color": "#ed8c25", "line-width": 4 },
        },
        before,
    );
    map.addLayer(
        {
            id: "coverage-points",
            type: "circle",
            source: "coverage",
            paint: {
                "circle-color": ["get", "color"],
                "circle-radius": 5,
                "circle-stroke-color": "#fff",
                "circle-stroke-width": 1.5,
            },
        },
        before,
    );
    update();
}
onMounted(() => {
    if (!container.value) return;
    const activeMap = new MapLibreMap({
        container: container.value,
        style: $q.dark.isActive ? darkStyle : lightStyle,
        center: props.initialView?.center ?? [8.2, 46.8],
        zoom: props.initialView?.zoom ?? 7,
        renderWorldCopies: false,
    });
    map = activeMap;
    activeMap.addControl(new NavigationControl(), "top-right");
    activeMap.on("style.load", installLayers);
    activeMap.on("load", () => {
        const [west, south, east, north] = props.bounds ?? [];
        if (!props.initialView && west != null && south != null && east != null && north != null)
            activeMap.fitBounds(
                [
                    [west, south],
                    [east, north],
                ],
                { padding: 45, maxZoom: 13, duration: 0 },
            );
        reportViewport();
    });
    activeMap.on("moveend", () => {
        clearTimeout(timer);
        timer = setTimeout(reportViewport, 200);
    });
    activeMap.on("click", event => {
        if (!activeMap.getLayer("route-lines")) return;
        const hits = activeMap.queryRenderedFeatures(
            [
                [event.point.x - 4, event.point.y - 4],
                [event.point.x + 4, event.point.y + 4],
            ],
            { layers: ["route-lines", "cell-hit"] },
        );
        const hit = hits.find(h => h.layer.id === "route-lines") ?? hits[0];
        const selected = props.items.find(item => featureKey(item) === hit?.properties.key);
        if (selected) emit("select", selected);
    });
    activeMap.on("error", () => {
        emit("error", t("system.mapPartial"));
    });
});
watch(
    () => [
        props.items,
        props.selected,
        props.points,
        props.coverageKind,
        props.now,
        props.maxAgeSeconds,
        props.roadBoxes,
        props.elevationBoxes,
        props.showRoads,
        props.showElevation,
    ],
    update,
);
watch(
    () => $q.dark.isActive,
    dark => map?.setStyle(dark ? darkStyle : lightStyle),
);
onBeforeUnmount(() => {
    clearTimeout(timer);
    map?.remove();
});
</script>

<template>
    <div ref="container" class="system-map" role="region" :aria-label="t('system.mapLabel')" />
</template>
<style scoped>
.system-map {
    width: 100%;
    height: 100%;
    min-height: 400px;
}
</style>
