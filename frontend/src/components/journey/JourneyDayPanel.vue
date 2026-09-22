<script setup lang="ts">
import { computed, ref, toRefs, watch } from "vue";
import { symSharpBed, symSharpCloudOff, symSharpStar, symSharpWarning } from "@quasar/extras/material-symbols-sharp";
import type { JourneyDayOut, JourneyOut, JourneyStageOut, PoiOut } from "@norain/api/models";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import NiceMap from "@/components/NiceMap.vue";
import WeatherChart from "@/components/WeatherChart.vue";
import { useJourneyStageForecast, useJourneyStagePois } from "@/queries/journeys";
import { clock, dayLabel, duration, km } from "@/utils/journeys";
import { poiCategory, poiName, type MapPoi } from "@/utils/poiCategories";
import { scoreColor } from "@/utils/rideQuality";

const props = defineProps<{
    journey: JourneyOut;
    day: JourneyDayOut;
}>();
const { journey, day } = toRefs(props);

const stages = computed<JourneyStageOut[]>(() => day.value.stages ?? []);
const preferred = computed(() => stages.value.find(s => s.recommended)?.id ?? stages.value[0]?.id ?? null);
const selectedId = ref<string | null>(null);
// Follow the recommendation until the user picks an alternative themselves.
const userPicked = ref(false);
watch(
    [preferred, () => day.value.id],
    ([id], [, oldDay]) => {
        if (oldDay !== day.value.id) userPicked.value = false;
        if (!userPicked.value || !stages.value.some(s => s.id === selectedId.value)) selectedId.value = id;
    },
    { immediate: true },
);
function pick(id: string) {
    selectedId.value = id;
    userPicked.value = true;
}
const stage = computed(() => stages.value.find(s => s.id === selectedId.value));

const forecastQuery = useJourneyStageForecast(
    () => journey.value.id,
    () => stage.value?.id,
    () => !!day.value.forecastAvailable,
);
const forecast = computed(() => forecastQuery.data.value);
const progressPercent = computed(() => {
    const progress = forecastQuery.progress.value;
    return progress?.cellsTotal ? Math.round((100 * progress.cellsSettled) / progress.cellsTotal) : undefined;
});
const selectedSample = ref(0);
watch(forecast, () => {
    selectedSample.value = 0;
});

// Everything on the way, not just the planned stops, when the user asks for it.
const showAllPois = ref(false);
const { data: wayPois } = useJourneyStagePois(
    () => journey.value.id,
    () => stage.value?.id,
    () => (showAllPois.value ? journey.value.poiCategories : []),
);

function mapPoi(poi: PoiOut, emphasis: boolean): MapPoi {
    return { lon: poi.lon, lat: poi.lat, category: poi.category, name: poi.name, emphasis };
}
const mapPois = computed<MapPoi[]>(() => {
    const planned = [
        ...(stage.value?.breaks ?? []).flatMap(b => (b.pois ?? []).map(p => mapPoi(p, true))),
        ...(stage.value?.detours ?? []).map(p => mapPoi(p, true)),
        ...(day.value.lodging ? [mapPoi(day.value.lodging, true)] : []),
    ];
    const seen = new Set(planned.map(p => `${p.lon},${p.lat}`));
    const extra = showAllPois.value
        ? (wayPois.value ?? []).filter(p => !seen.has(`${p.lon},${p.lat}`)).map(p => mapPoi(p, false))
        : [];
    return [...planned, ...extra];
});

const missingGaps = computed(() =>
    Object.entries(stage.value?.gaps ?? {})
        .filter(([category, gap]) => journey.value.poiCategories.includes(category) && gap > legLimitM.value)
        .map(([category, gap]) => `${poiCategory(category).label}: ${km(gap)} ohne`),
);
const legLimitM = computed(() => journey.value.maxLegDistanceM ?? Number.POSITIVE_INFINITY);

function breakEta(elapsedS: number): string {
    const departure = stage.value?.recommendedDeparture ?? stage.value?.departureTime;
    if (!departure) return duration(elapsedS);
    const start = new Date(departure).getTime();
    return new Date(start + elapsedS * 1000).toLocaleTimeString("de-CH", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Europe/Zurich",
    });
}
</script>

<template>
    <div class="column q-gutter-md">
        <div class="row q-col-gutter-md">
            <div class="col-12 col-md-4">
                <q-card class="full-height">
                    <q-card-section>
                        <div class="text-subtitle1 text-weight-bold">Tag {{ day.index + 1 }} · {{ dayLabel(day.date) }}</div>
                        <div v-if="stage" class="text-caption text-muted">
                            {{ km(stage.distanceM) }} · {{ duration(stage.totalSeconds) }} Fahrzeit
                        </div>
                        <q-chip v-if="day.weatherRouted" dense outline color="primary" class="q-ml-none q-mt-sm">
                            Um Regen und Gegenwind geplant
                        </q-chip>
                    </q-card-section>

                    <q-card-section class="q-pt-none">
                        <div v-if="day.lodging" class="row items-center no-wrap">
                            <q-icon :name="symSharpBed" class="q-mr-sm" />
                            <div>
                                <div class="text-body2">{{ poiName(day.lodging) }}</div>
                                <div class="text-caption text-muted">Übernachtung · {{ km(day.lodging.offsetM) }} neben der Strecke</div>
                            </div>
                        </div>
                        <q-banner v-else-if="day.lodgingMissing" dense rounded class="bg-tint-warn">
                            <template #avatar><q-icon :name="symSharpWarning" /></template>
                            Keine passende Unterkunft nahe der Strecke gefunden. Der Tag endet, wo das Tageslimit
                            erreicht ist.
                        </q-banner>
                    </q-card-section>

                    <template v-if="stages.length > 1">
                        <q-separator inset />
                        <q-card-section>
                            <div class="text-caption q-mb-xs">Varianten</div>
                            <q-list dense>
                                <q-item
                                    v-for="(alternative, n) in stages"
                                    :key="alternative.id"
                                    clickable
                                    :active="alternative.id === selectedId"
                                    active-class="bg-tint-wet"
                                    class="rounded-borders"
                                    @click="pick(alternative.id)"
                                >
                                    <q-item-section avatar>
                                        <span
                                            class="quality-swatch"
                                            :style="{ background: scoreColor(alternative.rideScore ?? null) }"
                                            aria-hidden="true"
                                        />
                                    </q-item-section>
                                    <q-item-section>
                                        <q-item-label>
                                            Variante {{ n + 1 }}
                                            <q-icon v-if="alternative.recommended" :name="symSharpStar" color="accent" size="xs" />
                                        </q-item-label>
                                        <q-item-label caption>
                                            {{ km(alternative.distanceM) }} · {{ duration(alternative.totalSeconds) }}
                                            <template v-if="alternative.rideLabel"> · {{ alternative.rideLabel }}</template>
                                        </q-item-label>
                                        <q-item-label v-for="reason in alternative.reasons" :key="reason" caption>
                                            {{ reason }}
                                        </q-item-label>
                                    </q-item-section>
                                    <q-item-section v-if="alternative.forecastStatus && alternative.forecastStatus !== 'done'" side>
                                        <q-spinner-dots size="1rem" />
                                    </q-item-section>
                                </q-item>
                            </q-list>
                        </q-card-section>
                    </template>

                    <template v-if="stage">
                        <q-separator inset />
                        <q-card-section>
                            <div v-if="stage.recommendedDeparture" class="text-body2 q-mb-sm">
                                Empfohlene Abfahrt: <b>{{ clock(stage.recommendedDeparture) }} Uhr</b>
                            </div>
                            <div class="text-caption q-mb-xs">Pausen</div>
                            <div v-if="!stage.breaks?.length" class="text-caption text-muted">Keine Pause nötig.</div>
                            <q-timeline v-else dense layout="dense" color="primary" class="q-my-none">
                                <q-timeline-entry
                                    v-for="stop in stage.breaks"
                                    :key="stop.alongM"
                                    :subtitle="`${km(stop.alongM)} · ${breakEta(stop.elapsedS)}`"
                                >
                                    <div v-if="!stop.pois?.length" class="text-caption text-muted">Hier gibt es nichts Gewünschtes.</div>
                                    <div v-for="poi in stop.pois" :key="poi.osmRef" class="text-caption">
                                        {{ poiCategory(poi.category).emoji }} {{ poiName(poi) }}
                                    </div>
                                </q-timeline-entry>
                            </q-timeline>
                            <div v-for="gap in missingGaps" :key="gap" class="text-caption text-negative">{{ gap }}</div>
                            <div v-for="detour in stage.detours" :key="detour.osmRef" class="text-caption text-muted">
                                Umweg zu {{ poiCategory(detour.category).emoji }} {{ poiName(detour) }}
                            </div>
                        </q-card-section>
                    </template>
                </q-card>
            </div>

            <template v-if="forecast">
                <div class="col-12 col-sm-6 col-md-4">
                    <q-card class="full-height">
                        <ForecastSummaryCard flat :forecast="forecast" />
                    </q-card>
                </div>
                <div class="col-12 col-sm-6 col-md-4">
                    <q-card class="full-height column">
                        <q-card-section class="col" style="min-height: 260px">
                            <WeatherChart
                                kind="temperature"
                                :version="forecast.version"
                                :selected-sample="selectedSample"
                                :samples="forecast.samples"
                                @select-sample="selectedSample = $event"
                            />
                        </q-card-section>
                    </q-card>
                </div>
            </template>
            <div v-else-if="!day.forecastAvailable" class="col-12 col-md-8">
                <q-banner rounded class="bg-tint-neutral">
                    <template #avatar><q-icon :name="symSharpCloudOff" class="text-muted" /></template>
                    Für diesen Tag gibt es noch keine Vorhersage. Plane die Reise näher am Termin neu, dann
                    richtet NoRain Strecke und Abfahrt nach dem Wetter.
                </q-banner>
            </div>
            <div v-else-if="forecastQuery.error.value" class="col-12 col-md-8">
                <q-banner rounded class="bg-tint-error">Wetterdaten konnten nicht geladen werden.</q-banner>
            </div>
        </div>

        <q-card v-if="forecast" class="column overflow-hidden" style="height: 55vh; min-height: 320px">
            <NiceMap
                :route-weather="forecast"
                :selected-sample="selectedSample"
                :pois="mapPois"
                @select-sample="selectedSample = $event"
            />
        </q-card>
        <q-toggle v-if="forecast" v-model="showAllPois" dense label="Alle gewünschten Orte entlang der Strecke zeigen" />

        <q-inner-loading :showing="forecastQuery.isFetching.value && !forecast">
            <q-circular-progress
                v-if="progressPercent !== undefined"
                show-value
                :value="progressPercent"
                size="3rem"
                :thickness="0.2"
                color="primary"
                track-color="grey-3"
            />
            <q-spinner-dots v-else size="3rem" color="primary" />
            <div class="text-muted q-mt-sm">Wetterdaten werden geladen…</div>
        </q-inner-loading>
    </div>
</template>

<style scoped>
.quality-swatch {
    display: inline-block;
    width: 14px;
    height: 14px;
    border-radius: 3px;
}
</style>
