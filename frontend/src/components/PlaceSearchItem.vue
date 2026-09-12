<script setup lang="ts">
import { computed, toRefs } from "vue";
import type { PlacesSearchResult } from "@norain/api/models";

const props = defineProps<{
    feature: PlacesSearchResult;
    focused: boolean;
    inline: boolean;
    clickable: boolean;
}>();
const emit = defineEmits<(e: "click") => void>();

const { feature, focused, inline, clickable } = toRefs(props);

const cantonAbbreviations: Record<string, string> = {
    Aargau: "AG",
    "Appenzell Innerrhoden": "AI",
    "Appenzell Ausserrhoden": "AR",
    Bern: "BE",
    "Basel-Landschaft": "BL",
    "Basel-Stadt": "BS",
    Fribourg: "FR",
    Genève: "GE",
    Glarus: "GL",
    Graubünden: "GR",
    Jura: "JU",
    Luzern: "LU",
    Neuchâtel: "NE",
    Nidwalden: "NW",
    Obwalden: "OW",
    "St. Gallen": "SG",
    Schaffhausen: "SH",
    Solothurn: "SO",
    Schwyz: "SZ",
    Thurgau: "TG",
    Ticino: "TI",
    Uri: "UR",
    Vaud: "VD",
    Valais: "VS",
    Zug: "ZG",
    Zürich: "ZH",
};

const classes = computed(() => (inline.value ? "q-mx-none q-px-none" : ""));

const getCityWithOptionalCanton = () => {
    const { city, state, showCanton } = feature.value.properties;
    if (!city) return "";

    if (showCanton) {
        const cantonAbbr = state ? cantonAbbreviations[state] : undefined;
        if (cantonAbbr) {
            return `${city} (${cantonAbbr})`;
        }
    }
    return city;
};
const secondaryLine = computed(() => {
    const { name, city, state } = feature.value.properties;

    if (name && name === city) {
        return state ?? "";
    }
    if (city && name !== city) {
        return getCityWithOptionalCanton();
    }
    return "";
});

const inlineDisplay = computed(() => {
    const { name, city } = feature.value.properties;
    if (!name) return "Unknown";

    const cityWithCanton = getCityWithOptionalCanton();

    if (name === city) {
        return cityWithCanton;
    }
    return cityWithCanton ? `${name}, ${cityWithCanton}` : name;
});
</script>

<template>
    <q-item :class="classes" :clickable="clickable" :focused="focused" @click="emit('click')">
        <q-item-section v-if="feature">
            <q-item-label v-if="inline">{{ inlineDisplay }}</q-item-label>
            <q-item-label v-else>{{ feature.properties.name }}</q-item-label>
            <q-item-label v-if="!inline" caption>{{ secondaryLine }}</q-item-label>
        </q-item-section>
    </q-item>
</template>

<style scoped></style>
