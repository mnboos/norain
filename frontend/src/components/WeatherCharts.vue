<script setup lang="ts">
import { defineAsyncComponent } from "vue";
import type { Data, Layout } from "plotly.js/lib/core";

// Loaded lazily so Plotly ends up in its own chunk, fetched only once a forecast is shown.
defineEmits<{ selectSample: [index: number] }>();

const NiceChart = defineAsyncComponent(() => import("./chart/NiceChart.vue"));

// The API client types the figures as opaque JSON dicts, so both parts are optional here.
defineProps<{
    figures: { data?: Data[]; layout?: Partial<Layout> }[];
}>();
</script>

<template>
    <div class="col-12 text-caption">
        Schattierung: 10.–90. Perzentil · Linie: Median · Gepunktet: Einzelprognose. Punkt auswählen für Details.
    </div>
    <!-- Fragment root: the columns drop straight into the caller's `row` grid. -->
    <div v-for="(fig, i) in figures" :key="i" class="col-12 col-sm-6 col-md-3">
        <q-card flat bordered class="square-tile">
            <NiceChart :figure="fig" @select-sample="$emit('selectSample', $event)" />
        </q-card>
    </div>
</template>
