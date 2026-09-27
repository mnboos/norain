<script setup lang="ts">
import { onBeforeUnmount, onMounted, useTemplateRef, watch } from "vue";
import { useQuasar } from "quasar";
import { GeoJSONSource, LngLatBounds, Map, config } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import styleUrl from "@/assets/map-styles/positron.json?url";
import { alternativeColor } from "@/utils/rideQuality";

/** A random ride's variants side by side, each in its own colour; the picked ones drawn bold. */
const props = defineProps<{ paths: number[][][]; picked: boolean[] }>();
const emit = defineEmits<{ toggle: [index: number] }>();

const $q = useQuasar();
const container = useTemplateRef<HTMLDivElement>("container");
let map: Map | undefined;
let loaded = false;
let drawnLayers: string[] = [];

function features() {
    return {
        type: "FeatureCollection" as const,
        features: props.paths.map((coordinates, index) => ({
            type: "Feature" as const,
            properties: {
                index,
                picked: props.picked[index] === true,
                color: alternativeColor(index, $q.dark.isActive),
            },
            geometry: { type: "LineString" as const, coordinates },
        })),
    };
}

function draw(fit: boolean) {
    if (!map || !loaded) return;
    const source = map.getSource<GeoJSONSource>("variants");
    if (source) void source.setData(features());
    else map.addSource("variants", { type: "geojson", data: features() });
    for (const id of drawnLayers) if (map.getLayer(id)) map.removeLayer(id);
    // Unpicked first, so a picked variant is on top where they share road.
    drawnLayers = [false, true].map(picked => {
        const id = `variants-${picked ? "picked" : "rest"}`;
        map?.addLayer({
            id,
            type: "line",
            source: "variants",
            filter: ["==", ["get", "picked"], picked],
            layout: { "line-cap": "round", "line-join": "round" },
            paint: {
                "line-color": ["get", "color"],
                "line-width": picked ? 6 : 3,
                "line-opacity": picked ? 1 : 0.55,
            },
        });
        return id;
    });
    if (!fit) return;
    const bounds = new LngLatBounds();
    for (const path of props.paths)
        for (const p of path) if (p[0] !== undefined && p[1] !== undefined) bounds.extend([p[0], p[1]]);
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 30, duration: 0, maxZoom: 15 });
}

onMounted(() => {
    if (!container.value) return;
    config.WORKER_URL = workerUrl;
    map = new Map({ container: container.value, style: styleUrl, center: [8.5, 47.3], zoom: 8 });
    map.on("load", () => {
        loaded = true;
        draw(true);
    });
    // Tapping a line picks or unpicks its variant; the list beside the map does the same.
    map.on("click", event => {
        const hit = map?.queryRenderedFeatures(event.point, { layers: drawnLayers })[0];
        const index: unknown = hit?.properties.index;
        if (typeof index === "number") emit("toggle", index);
    });
});
watch(
    () => props.paths,
    () => {
        draw(true);
    },
);
watch(
    () => props.picked,
    () => {
        draw(false);
    },
    { deep: true },
);
onBeforeUnmount(() => map?.remove());
</script>

<template>
    <div ref="container" class="variants-map" role="img" aria-label="Karte der Varianten" />
</template>

<style scoped>
.variants-map {
    height: 360px;
    min-height: 240px;
    border-radius: 4px;
}
</style>
