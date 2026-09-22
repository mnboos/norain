<script setup lang="ts">
import { onMounted, onBeforeUnmount, useTemplateRef, watch } from "vue";
import { Map, LngLatBounds, GeoJSONSource, config } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import styleUrl from "@/assets/map-styles/positron.json?url";
const props = defineProps<{ original: number[][]; calculated?: number[][] }>();
const container = useTemplateRef<HTMLDivElement>("container");
let map: Map | undefined;
let loaded = false;
function draw() {
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
    const bounds = new LngLatBounds();
    for (const p of [...props.original, ...(props.calculated ?? [])]) if (p[0] !== undefined && p[1] !== undefined) bounds.extend([p[0], p[1]]);
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 30, duration: 0, maxZoom: 15 });
}
onMounted(() => {
    if (!container.value) return;
    config.WORKER_URL = workerUrl;
    map = new Map({ container: container.value, style: styleUrl, center: [8.5, 47.3], zoom: 8 });
    map.on("load", () => { loaded = true; draw(); });
});
watch(() => [props.original, props.calculated], draw);
onBeforeUnmount(() => map?.remove());
</script>
<template><div ref="container" style="height: 300px; min-height: 220px" aria-label="Vorschau der importierten Strecke" /></template>
