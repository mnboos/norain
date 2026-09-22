<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ modelValue: number; distanceM: number }>();
const emit = defineEmits<{ "update:modelValue": [seconds: number] }>();
const speed = computed(() => props.modelValue > 0 ? Math.round(props.distanceM / props.modelValue * 3.6 * 10) / 10 : 0);
function setSpeed(value: string | number | null) {
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) emit("update:modelValue", Math.max(1, Math.round(props.distanceM / (n / 3.6))));
}
function setMinutes(value: string | number | null) {
    const n = Number(value);
    emit("update:modelValue", Number.isFinite(n) && n > 0 ? Math.round(n * 60) : 0);
}
</script>
<template>
    <div class="row q-col-gutter-sm">
        <q-input
class="col-6" outlined dense type="number" label="Ø Geschwindigkeit" suffix="km/h"
            :model-value="speed" min="0.1" step="0.1" @update:model-value="setSpeed" />
        <q-input
class="col-6" outlined dense type="number" label="Fahrzeit" suffix="min"
            :model-value="Math.round(modelValue / 60 * 10) / 10" min="0.1" step="1" @update:model-value="setMinutes" />
    </div>
</template>
