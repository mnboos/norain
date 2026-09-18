<script setup lang="ts">
import { watch } from "vue";
import { useEntitlements } from "@/composables/useEntitlements";
const { isPro } = useEntitlements();
const before = defineModel<number>("before", { default: 0 });
const after = defineModel<number>("after", { default: 0 });
watch(isPro, value => { if (!value) { before.value = 0; after.value = 0; } }, { immediate: true });
const options = Array.from({ length: 9 }, (_, i) => ({
    label: i === 0 ? "Nicht flexibel" : `${i * 15} min`,
    value: i * 15,
}));
</script>

<template>
    <q-expansion-item
        label="Erweitert"
        :caption="before || after ? `Flexibel: ${before} min früher · ${after} min später` : undefined"
        dense
    >
        <div v-if="!isPro" class="q-pa-sm">
            <p>Finde mit Plus eine bessere Abfahrtszeit. Deine normale Vorhersage bleibt kostenlos.</p>
            <q-btn to="/account" color="primary" label="Plus 14 Tage kostenlos testen" no-caps flat />
        </div>
        <div v-else class="q-pa-sm">
            <div class="text-h6">Flexible Abfahrtszeit</div>
            <p class="text-body2">Vergleiche das Wetter für frühere oder spätere Abfahrten.</p>
            <div class="row q-col-gutter-sm">
                <q-select
                    v-model="before"
                    class="col"
                    label="Früher um bis zu"
                    :options="options"
                    emit-value
                    map-options
                    outlined
                    dense
                />
                <q-select
                    v-model="after"
                    class="col"
                    label="Später um bis zu"
                    :options="options"
                    emit-value
                    map-options
                    outlined
                    dense
                />
            </div>
            <slot />
        </div>
    </q-expansion-item>
</template>
