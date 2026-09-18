<script setup lang="ts">
const before = defineModel<number>("before", { default: 0 });
const after = defineModel<number>("after", { default: 0 });
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
        <div class="q-pa-sm">
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
