<script setup lang="ts">
import { ref } from "vue";

const open = ref(false);
const before = defineModel<number>("before", { default: 0 });
const after = defineModel<number>("after", { default: 0 });
const options = Array.from({ length: 9 }, (_, i) => ({
    label: i === 0 ? "Nicht flexibel" : `${i * 15} min`,
    value: i * 15,
}));
</script>

<template>
    <div>
        <q-btn flat dense no-caps label="Erweitert" @click="open = true" />
        <span v-if="before || after" class="text-caption text-muted q-ml-sm">
            Flexibel: {{ before }} min früher · {{ after }} min später
        </span>
        <q-dialog v-model="open">
            <q-card style="width: 460px; max-width: 92vw">
                <q-card-section>
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
                </q-card-section>
                <q-card-actions align="right">
                    <q-btn v-close-popup flat no-caps label="Fertig" color="primary" />
                </q-card-actions>
            </q-card>
        </q-dialog>
    </div>
</template>
