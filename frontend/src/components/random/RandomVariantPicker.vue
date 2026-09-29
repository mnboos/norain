<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { symSharpSave } from "@quasar/extras/material-symbols-sharp";
import type { JourneyOut, JourneyStageOut } from "@norain/api/models";
import ElevationChart from "@/components/ElevationChart.vue";
import VariantsMap from "@/components/random/VariantsMap.vue";
import { useSaveVariantsAsRoutes } from "@/queries/journeys";
import { isQuotaExceeded } from "@/services/http";
import { duration, km } from "@/utils/journeys";
import { type MapPoi, poiCategory } from "@/utils/poiCategories";
import { alternativeColor } from "@/utils/rideQuality";
import WeekdaySelector from "@/components/WeekdaySelector.vue";
import { cronWeekday, weeklyCron, weeklyDescription } from "@/utils/weeklySchedule";

/**
 * A random ride without the weather mode: its variants, no forecast. The rider picks one or more
 * and saves each as a route of their own, which is where the forecast then comes from.
 */
const props = defineProps<{ ride: JourneyOut }>();

const $q = useQuasar();
const { t } = useI18n();
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

// All variants' profiles in one chart, in the map's colours: the first picked one (else the
// first variant) drawn as the main line, the others beside it.
const mainIndex = computed(() => Math.max(0, picked.value.indexOf(true)));
const mainProfile = computed(() => {
    const stage = stages.value[mainIndex.value];
    return stage
        ? {
              stageId: stage.id,
              color: alternativeColor(mainIndex.value, $q.dark.isActive),
              label: t("journeyDay.variant", { n: mainIndex.value + 1 }),
          }
        : null;
});
const otherProfiles = computed(() =>
    stages.value.flatMap((stage, index) =>
        index === mainIndex.value
            ? []
            : [{ stageId: stage.id, color: alternativeColor(index, $q.dark.isActive), label: t("journeyDay.variant", { n: index + 1 }) }],
    ),
);

// The stops the planner routed each variant through, for the categories the rider asked for.
function stops(stage: JourneyStageOut) {
    return [...(stage.breaks ?? []).flatMap(b => b.pois ?? []), ...(stage.detours ?? [])];
}
const pois = computed<MapPoi[]>(() => {
    const anyPicked = picked.value.includes(true);
    const all = stages.value.flatMap((stage, index) =>
        stops(stage).map(poi => ({
            osmRef: poi.osmRef,
            lon: poi.lon,
            lat: poi.lat,
            category: poi.category,
            name: poi.name,
            planned: !anyPicked || picked.value[index] === true,
            note: t("journeyDay.variant", { n: index + 1 }),
        })),
    );
    // One marker per OSM object, a picked variant's first.
    const seen = new Set<string>();
    return [...all.filter(p => p.planned), ...all.filter(p => !p.planned)].filter(
        p => !seen.has(p.osmRef) && seen.add(p.osmRef),
    );
});
const wanted = computed(() => props.ride.poiCategories);
function stopSummary(stage: JourneyStageOut): { visited: string[]; missing: string[] } {
    const visited = new Set(stops(stage).map(poi => poi.category));
    return {
        visited: wanted.value.filter(category => visited.has(category)),
        missing: wanted.value.filter(category => !visited.has(category)),
    };
}

function toggle(index: number) {
    picked.value = picked.value.map((value, i) => (i === index ? !value : value));
}

const name = ref(props.ride.name);
// The ride's own day and departure, as a weekly schedule the rider can change.
const days = ref<number[]>([cronWeekday(props.ride.startDate)]);
const time = ref(props.ride.earliestStart.slice(0, 5));
const scheduleCron = computed(() => weeklyCron(days.value, time.value));
const scheduleDescription = computed(() => weeklyDescription(days.value, time.value));

function routeName(index: number): string {
    const base = name.value.trim() || props.ride.name;
    if (pickedIds.value.length === 1) return base;
    const variant = stages.value.findIndex(stage => stage.id === pickedIds.value[index]);
    return `${base} – ${t("journeyDay.variant", { n: variant + 1 })}`;
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
            message: t("variants.saved", routes.length),
        });
        await router.push(routes.length === 1 && routes[0] ? `/routes/${routes[0].id}` : "/routes");
    } catch (err: unknown) {
        if (isQuotaExceeded(err)) {
            $q.dialog({
                title: t("quota.title"),
                message: t("variants.quota"),
                cancel: { label: t("variants.toMyRoutes"), flat: true },
                ok: { label: t("quota.upgrade"), color: "primary", unelevated: true },
            })
                .onOk(() => void router.push("/account"))
                .onCancel(() => void router.push("/routes"));
            return;
        }
        $q.notify({ type: "negative", message: t("variants.saveFailed") });
    }
}
</script>

<template>
    <div class="row q-col-gutter-md" data-testid="variant-picker">
        <div class="col-12 col-md-7">
            <VariantsMap :paths="paths" :picked="picked" :pois="pois" @toggle="toggle" />
            <ElevationChart
                v-if="mainProfile"
                class="q-mt-md"
                :stage-id="mainProfile.stageId"
                :color="mainProfile.color"
                :label="mainProfile.label"
                :alternatives="otherProfiles"
                compact
                data-testid="variant-elevation"
            />
        </div>
        <div class="col-12 col-md-5 q-gutter-md">
            <div>
                <div class="text-subtitle2">{{ t("variants.title") }}</div>
                <div class="text-caption text-muted">{{ t("variants.intro") }}</div>
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
                            <span class="text-weight-medium">{{ t("journeyDay.variant", { n: index + 1 }) }}</span>
                        </q-item-label>
                        <q-item-label caption>
                            {{ km(stage.distanceM) }} · {{ duration(stage.totalSeconds) }}
                            <template v-if="stage.ascentM != null">· {{ stage.ascentM }} m ↑</template>
                            <template v-if="stage.breaks?.length">· {{ t("journeyDay.stops", stage.breaks.length) }}</template>
                        </q-item-label>
                        <q-item-label v-if="wanted.length" caption data-testid="variant-stops">
                            <span
                                v-for="category in stopSummary(stage).visited"
                                :key="category"
                                :title="poiCategory(category).label"
                                :aria-label="poiCategory(category).label"
                                role="img"
                                class="q-mr-xs"
                            >
                                {{ poiCategory(category).emoji }}
                            </span>
                            <span v-if="stopSummary(stage).missing.length" class="text-warning">
                                {{
                                    t("variants.missing", {
                                        categories: stopSummary(stage)
                                            .missing.map(c => poiCategory(c).label)
                                            .join(", "),
                                    })
                                }}
                            </span>
                        </q-item-label>
                    </q-item-section>
                </q-item>
            </q-list>

            <q-input v-model="name" :label="t('routeForm.name')" outlined dense />

            <div>
                <div class="text-caption q-mb-xs">{{ t("variants.when") }}</div>
                <WeekdaySelector v-model="days" />
                <q-input
                    v-model="time"
                    class="q-mt-sm"
                    :label="t('routeForm.departure')"
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
                :label="t('variants.save', pickedIds.length)"
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
