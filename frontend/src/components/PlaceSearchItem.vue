<script setup lang="ts">
import { computed, toRefs } from "vue";
import type { PlacesSearchResult } from "@norain/api/models";
import { placeSecondaryLine } from "@/utils/placeLabel";

const props = defineProps<{
    feature: PlacesSearchResult;
    focused: boolean;
    clickable: boolean;
}>();
const emit = defineEmits<(e: "click") => void>();

const { feature, focused, clickable } = toRefs(props);

const secondaryLine = computed(() => placeSecondaryLine(feature.value));
</script>

<template>
    <q-item :clickable="clickable" :focused="focused" @click="emit('click')">
        <q-item-section v-if="feature">
            <q-item-label>{{ feature.properties.name }}</q-item-label>
            <q-item-label caption>{{ secondaryLine }}</q-item-label>
        </q-item-section>
    </q-item>
</template>

<style scoped></style>
