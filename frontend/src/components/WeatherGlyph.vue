<script setup lang="ts">
import { computed } from "vue";
import { isNightEta, weatherIconSvg } from "@/utils/weatherIcons";

/** The weather glyph of one sample, drawn the same way as the chips on the map. */
const props = withDefaults(
    defineProps<{
        weatherCode?: number | null;
        rainMm: number;
        eta: string;
        label: string;
        size?: string;
    }>(),
    { weatherCode: null, size: "72px" },
);

const glyph = computed(() => weatherIconSvg(props.weatherCode, { rainMm: props.rainMm, night: isNightEta(props.eta) }));
</script>

<template>
    <!-- The markup is one of the fixed glyphs in utils/weatherIcons.ts, never data from outside. -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <svg :width="size" :height="size" viewBox="0 0 24 24" role="img" :aria-label="label" v-html="glyph" />
</template>
