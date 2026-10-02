<script setup lang="ts">
import { onMounted, onBeforeUnmount, useTemplateRef, watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { Map, LngLatBounds, GeoJSONSource, config } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { applyBasemap, BasemapControl, useBasemap } from "@/map/basemap";

const { t } = useI18n();
const $q = useQuasar();
const basemap = useBasemap();
const props = defineProps<{ original: number[][]; calculated?: number[][] }>();
const container = useTemplateRef<HTMLDivElement>("container");
let map: Map | undefined;
let loaded = false;
function draw(fit = true) {
    if (!map || !loaded) return;
    for (const [id, coordinates, color] of [
        ["original", props.original, "#a25219"], ["calculated", props.calculated ?? [], "#2563eb"],
    ] as const) {
        const data = { type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates } };
        const source = map.getSource<GeoJSONSource>(id);
        if (source) void source.setData(data);
        else {
            map.addSource(id, { type: "geojson", data });
            map.addLayer({ id, type: "line", source: id, paint: { "line-color": color, "line-width": id === "original" ? 6 : 3 } });
        }
    }
    if (!fit) return;
    const bounds = new LngLatBounds();
    for (const p of [...props.original, ...(props.calculated ?? [])]) if (p[0] !== undefined && p[1] !== undefined) bounds.extend([p[0], p[1]]);
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 30, duration: 0, maxZoom: 15 });
}
onMounted(() => {
    if (!container.value) return;
    config.WORKER_URL = workerUrl;
    map = new Map({ container: container.value, center: [8.5, 47.3], zoom: 8 });
    applyBasemap(map, basemap.value, $q.dark.isActive);
    map.addControl(new BasemapControl(next => t(next === "satellite" ? "map.basemapSatellite" : "map.basemapMap")), "top-right");
    map.on("load", () => { loaded = true; draw(); });
});
watch(() => [props.original, props.calculated], () => { draw(); });
// A new style drops the lines; put them back without moving the view.
watch([basemap, () => $q.dark.isActive], ([kind, dark]) => {
    if (map) applyBasemap(map, kind, dark, () => { draw(false); });
});
onBeforeUnmount(() => map?.remove());
</script>
<template><div ref="container" style="height: 300px; min-height: 220px" :aria-label="t('gpx.previewLabel')" /></template>
