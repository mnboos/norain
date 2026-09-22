<script setup lang="ts">
import { GeometrySource } from "@norain/api/models";
import { computed, ref, watch } from "vue";
import type { GpxPathOut, RoutePlanIn, RoutePlanOut } from "@norain/api/models";
import { gpxApi, gpxError, routingProfile, type RouteDraft } from "@/services/gpx";
import GpxPreviewMap from "./GpxPreviewMap.vue";
import RouteTimingFields from "./RouteTimingFields.vue";
import RouteEditorDialog from "./RouteEditorDialog.vue";
import { toLonLat } from "@/utils/routeEditing";
const props = defineProps<{ modelValue: boolean; profile?: string }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; apply: [draft: RouteDraft] }>();
const paths = ref<GpxPathOut[]>([]);
const selected = ref(0);
const mode = ref<GeometrySource>(GeometrySource.Imported);
const name = ref("");
const duration = ref(0);
const routingPoints = ref<number[][]>([]);
const preview = ref<RoutePlanOut>();
const loading = ref(false);
const error = ref("");
const editing = ref(false);
const file = ref<File | null>(null);
let generation = 0;
const path = computed(() => paths.value[selected.value]);
const plan = computed<RoutePlanIn>(() => ({ name: name.value, geometrySource: mode.value,
    coordinates: mode.value === GeometrySource.Imported ? path.value?.coordinates ?? [] : routingPoints.value,
    durationSeconds: mode.value === GeometrySource.Imported ? duration.value : null,
    profile: routingProfile(props.profile ?? "bike") }));
const valid = computed(() => !!path.value && !!name.value.trim() && duration.value >= 1 && duration.value <= 1382400);
watch(() => props.modelValue, open => {
    if (!open) { generation++; return; }
    paths.value = []; selected.value = 0; mode.value = GeometrySource.Imported; error.value = ""; file.value = null;
});
watch(path, value => {
    if (!value) return;
    name.value = value.name;
    duration.value = Math.max(1, Math.round(value.distanceM / (20 / 3.6)));
    routingPoints.value = value.routingPoints;
});
async function upload(value: File | null) {
    if (!value) return;
    const current = ++generation;
    error.value = ""; loading.value = true;
    try {
        if (value.size > 10 * 1024 * 1024) throw new Error("Die GPX-Datei darf höchstens 10 MiB gross sein.");
        const result = await gpxApi.coreApiGpxImportGpx({ file: value });
        if (current !== generation) return;
        selected.value = 0; paths.value = result;
    } catch (e) { if (current === generation) error.value = await gpxError(e); }
    finally { if (current === generation) loading.value = false; }
}
function drop(event: DragEvent) {
    const value = event.dataTransfer?.files[0];
    if (value) { file.value = value; void upload(value); }
}
watch([plan, valid], async () => {
    const current = ++generation;
    preview.value = undefined;
    const selectedPath = path.value;
    if (!valid.value || !selectedPath) { loading.value = false; return; }
    if (mode.value === GeometrySource.Imported) {
        preview.value = { coordinates: selectedPath.coordinates, distanceM: selectedPath.distanceM, timeS: duration.value };
        loading.value = false; error.value = ""; return;
    }
    loading.value = true; error.value = "";
    try {
        const result = await gpxApi.coreApiGpxPreviewGpx({ routePlanIn: plan.value });
        if (current === generation) preview.value = result;
    } catch (e) { if (current === generation) error.value = await gpxError(e); }
    finally { if (current === generation) loading.value = false; }
});
function apply() {
    if (!preview.value || !valid.value || loading.value) return;
    emit("apply", { plan: plan.value, preview: preview.value });
    emit("update:modelValue", false);
}
function adjust(points: number[][]) {
    const first = routingPoints.value[0], last = routingPoints.value.at(-1);
    if (first && last) routingPoints.value = [first, ...points, last];
}
</script>
<template>
    <q-dialog :model-value="modelValue" :maximized="$q.screen.xs" @update:model-value="emit('update:modelValue', $event)">
        <q-card style="width: 760px; max-width: 96vw" @dragover.prevent @drop.prevent="drop">
            <q-card-section class="text-h6">GPX importieren</q-card-section>
            <q-card-section class="q-gutter-md">
                <q-file v-model="file" accept=".gpx,application/gpx+xml" outlined label="GPX-Datei wählen oder hier ablegen" @update:model-value="upload" />
                <q-select
v-if="paths.length > 1" v-model="selected" outlined label="Strecke oder Abschnitt"
                    :options="paths.map((p, i) => ({ label: p.name, value: i }))" emit-value map-options />
                <template v-if="path">
                    <q-input v-model="name" outlined dense label="Name" maxlength="200" />
                    <q-option-group
v-model="mode" :options="[
                        { label: 'Originalstrecke behalten', value: 'imported' },
                        { label: 'Fürs Velo neu berechnen', value: 'graphhopper' },
                    ]" />
                    <RouteTimingFields v-if="mode === 'imported'" v-model="duration" :distance-m="path.distanceM" />
                    <div v-if="mode === 'imported'" class="text-caption">Die Wettervorhersage verwendet deine neue Abfahrtszeit und diese Fahrzeit.</div>
                    <div v-else class="text-caption">Orange: Original. Blau: neu berechnete Strecke. Die Strecke kann vom Original abweichen.</div>
                    <GpxPreviewMap :original="path.coordinates" :calculated="mode === 'graphhopper' ? preview?.coordinates : undefined" />
                    <div>{{ ((preview?.distanceM ?? path.distanceM) / 1000).toFixed(1) }} km
                        <template v-if="preview"> · {{ Math.round(preview.timeS / 60) }} min</template></div>
                    <q-btn v-if="mode === 'graphhopper'" outline no-caps label="Zwischenpunkte anpassen" @click="editing = true" />
                    <RouteEditorDialog
v-if="routingPoints.length >= 2" v-model="editing"
                        :start="toLonLat(routingPoints[0]!)" :dest="toLonLat(routingPoints[routingPoints.length - 1]!)"
                        :profile="profile ?? 'bike'" :via-points="routingPoints.slice(1, -1)" :original-coordinates="path.coordinates" @apply="adjust" />
                </template>
                <q-linear-progress v-if="loading" indeterminate />
                <div v-if="error" role="alert" class="text-negative">{{ error }}</div>
            </q-card-section>
            <q-card-actions align="right">
                <q-btn flat no-caps label="Abbrechen" @click="emit('update:modelValue', false)" />
                <q-btn color="primary" no-caps label="Übernehmen" :disable="!preview || !valid || loading" @click="apply" />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
