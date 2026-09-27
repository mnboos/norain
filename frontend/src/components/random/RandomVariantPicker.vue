<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuasar } from "quasar";
import { useRouter } from "vue-router";
import { symSharpSave } from "@quasar/extras/material-symbols-sharp";
import type { JourneyOut } from "@norain/api/models";
import VariantsMap from "@/components/random/VariantsMap.vue";
import { useSaveVariantsAsRoutes } from "@/queries/journeys";
import { isQuotaExceeded } from "@/services/http";
import { duration, km } from "@/utils/journeys";
import { alternativeColor } from "@/utils/rideQuality";
import { WEEKDAY_LABELS, cronWeekday, weeklyCron, weeklyDescription } from "@/utils/weeklySchedule";

/**
 * A random ride without the weather mode: its variants, no forecast. The rider picks one or more
 * and saves each as a route of their own, which is where the forecast then comes from.
 */
const props = defineProps<{ ride: JourneyOut }>();

const $q = useQuasar();
const router = useRouter();
const save = useSaveVariantsAsRoutes();

const stages = computed(() => props.ride.days?.[0]?.stages ?? []);
const paths = computed(() => stages.value.map(stage => stage.path));
const picked = ref<boolean[]>([]);
watch(
    stages,
    value => {
        picked.value = value.map(() => false);
    },
    { immediate: true },
);
const pickedIds = computed(() => stages.value.filter((_, i) => picked.value[i]).map(stage => stage.id));

function toggle(index: number) {
    picked.value = picked.value.map((value, i) => (i === index ? !value : value));
}

const name = ref(props.ride.name);
// The ride's own day and departure, as a weekly schedule the rider can change.
const days = ref<number[]>([cronWeekday(props.ride.startDate)]);
const time = ref(props.ride.earliestStart.slice(0, 5));
function toggleDay(day: number) {
    days.value = days.value.includes(day) ? days.value.filter(d => d !== day) : [...days.value, day].sort();
}
const scheduleCron = computed(() => weeklyCron(days.value, time.value));
const scheduleDescription = computed(() => weeklyDescription(days.value, time.value));

function routeName(index: number): string {
    const base = name.value.trim() || props.ride.name;
    if (pickedIds.value.length === 1) return base;
    const variant = stages.value.findIndex(stage => stage.id === pickedIds.value[index]);
    return `${base} – Variante ${variant + 1}`;
}

const canSave = computed(() => pickedIds.value.length > 0 && !!scheduleCron.value && !save.isPending.value);

async function onSave() {
    const ids = pickedIds.value;
    try {
        const routes = await save.mutateAsync({
            journeyId: props.ride.id,
            stageIds: ids,
            name: routeName,
            scheduleCron: scheduleCron.value,
            scheduleDescription: scheduleDescription.value,
        });
        $q.notify({
            type: "positive",
            message: routes.length === 1 ? "Route gespeichert." : `${routes.length} Routen gespeichert.`,
        });
        await router.push(routes.length === 1 && routes[0] ? `/routes/${routes[0].id}` : "/");
    } catch (err: unknown) {
        if (isQuotaExceeded(err)) {
            $q.dialog({
                title: "Tarifgrenze erreicht",
                message:
                    "Nicht alle Varianten passen in deinen Tarif. Was Platz hatte, ist gespeichert. " +
                    "Mit Plus hast du bis zu 20 Routen.",
                cancel: { label: "Zu meinen Routen", flat: true },
                ok: { label: "Upgrade", color: "primary", unelevated: true },
            })
                .onOk(() => void router.push("/account"))
                .onCancel(() => void router.push("/"));
            return;
        }
        $q.notify({ type: "negative", message: "Die Routen konnten nicht gespeichert werden." });
    }
}
</script>

<template>
    <div class="row q-col-gutter-md" data-testid="variant-picker">
        <div class="col-12 col-md-7">
            <VariantsMap :paths="paths" :picked="picked" @toggle="toggle" />
        </div>
        <div class="col-12 col-md-5 q-gutter-md">
            <div>
                <div class="text-subtitle2">Wähle deine Varianten</div>
                <div class="text-caption text-muted">
                    Jede gewählte Variante wird eine eigene Route. Die Vorhersage siehst du dann bei der Route.
                </div>
            </div>
            <q-list bordered separator class="rounded-borders">
                <q-item v-for="(stage, index) in stages" :key="stage.id" tag="label" clickable>
                    <q-item-section side>
                        <q-checkbox :model-value="picked[index] === true" @update:model-value="toggle(index)" />
                    </q-item-section>
                    <q-item-section>
                        <q-item-label class="row items-center q-gutter-x-sm">
                            <span
                                class="swatch"
                                :style="{ background: alternativeColor(index, $q.dark.isActive) }"
                                aria-hidden="true"
                            />
                            <span class="text-weight-medium">Variante {{ index + 1 }}</span>
                        </q-item-label>
                        <q-item-label caption>
                            {{ km(stage.distanceM) }} · {{ duration(stage.totalSeconds) }}
                            <template v-if="stage.breaks?.length">· {{ stage.breaks.length }} Stopps</template>
                        </q-item-label>
                    </q-item-section>
                </q-item>
            </q-list>

            <q-input v-model="name" label="Name" outlined dense />

            <div>
                <div class="text-caption q-mb-xs">Wann fährst du?</div>
                <div class="row q-gutter-xs">
                    <q-btn
                        v-for="(label, i) in WEEKDAY_LABELS"
                        :key="label"
                        :label="label"
                        size="sm"
                        dense
                        no-caps
                        :unelevated="days.includes(i + 1)"
                        :outline="!days.includes(i + 1)"
                        color="primary"
                        :aria-pressed="days.includes(i + 1)"
                        @click="toggleDay(i + 1)"
                    />
                </div>
                <q-input
                    v-model="time"
                    class="q-mt-sm"
                    label="Abfahrt"
                    outlined
                    dense
                    mask="##:##"
                    fill-mask
                    style="max-width: 140px"
                />
                <div v-if="scheduleDescription" class="text-caption text-muted q-mt-xs">{{ scheduleDescription }}</div>
            </div>

            <q-btn
                color="primary"
                unelevated
                no-caps
                :icon="symSharpSave"
                :label="pickedIds.length > 1 ? `${pickedIds.length} Routen speichern` : 'Als Route speichern'"
                :disable="!canSave"
                :loading="save.isPending.value"
                data-testid="save-variants"
                @click="onSave"
            />
        </div>
    </div>
</template>

<style scoped>
.swatch {
    display: inline-block;
    width: 18px;
    height: 4px;
    border-radius: 2px;
}
</style>
