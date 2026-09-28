<script setup lang="ts">
import { useRoutePosition } from "@/composables/useRoutePosition";
import ElevationChart from "@/components/ElevationChart.vue";
import { computed, ref, toRefs, watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { symSharpBed, symSharpCloudOff, symSharpStar, symSharpWarning } from "@quasar/extras/material-symbols-sharp";
import {
    JourneyReasonOutKindEnum,
    type JourneyDayOut,
    type JourneyOut,
    type JourneyStageOut,
    type PoiOut,
} from "@norain/api/models";
import ForecastFreshness from "@/components/ForecastFreshness.vue";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import NiceMap from "@/components/NiceMap.vue";
import WeatherChart from "@/components/WeatherChart.vue";
import { useJourneyStageForecast, useJourneyStageForecasts, useJourneyStagesPois } from "@/queries/journeys";
import { clock, dayLabel, duration, gapExcessLabel, isPlanningWarning, km, reasonText } from "@/utils/journeys";
import { intlLocale } from "@/i18n";
import { rideLabelText } from "@/utils/levels";
import { CANDIDATE_COLOR, poiCategory, poiName, type MapPoi } from "@/utils/poiCategories";
import { alternativeColor } from "@/utils/rideQuality";

const $q = useQuasar();
const { t } = useI18n();
const props = defineProps<{
    journey: JourneyOut;
    day: JourneyDayOut;
}>();
const { journey, day } = toRefs(props);

const stages = computed<JourneyStageOut[]>(() => day.value.stages ?? []);
const preferred = computed(() => stages.value.find(s => s.recommended)?.id ?? stages.value[0]?.id ?? null);
const selectedId = ref<string | null>(null);
const detailsOpen = ref(false);
watch(
    () => day.value.id,
    () => {
        detailsOpen.value = false;
    },
);
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
// The variants not picked, each in its own colour on the map (by its place in the list, so a
// variant keeps its colour whichever one is picked); a click on one picks it. Each brings its
// own forecast's wind for the animation, once that forecast is done: waiting on the rest would
// open a job socket per variant.
const forecastedAlternatives = computed(() =>
    day.value.forecastAvailable
        ? stages.value.filter(s => s.id !== selectedId.value && s.forecastStatus === "done").map(s => s.id)
        : [],
);
const alternativeForecasts = useJourneyStageForecasts(() => journey.value.id, forecastedAlternatives);
const alternativeLines = computed(() => {
    const windArrows = new Map(
        forecastedAlternatives.value.map((id, n) => [id, alternativeForecasts.value[n]?.data?.windArrows] as const),
    );
    return stages.value
        .map((s, index) => ({ id: s.id, line: s.path, index, windArrows: windArrows.get(s.id) }))
        .filter(s => s.id !== selectedId.value);
});
function variantColor(index: number): string {
    return alternativeColor(index, $q.dark.isActive);
}
// The elevation profiles in each variant's own colour, the picked one too (its line on the map
// is coloured by ride quality instead, but the profile needs a colour of its own).
const selectedIndex = computed(() =>
    Math.max(
        0,
        stages.value.findIndex(s => s.id === selectedId.value),
    ),
);
const selectedProfileColor = computed(() => variantColor(selectedIndex.value));
const selectedLabel = computed(() => t("journeyDay.variant", { n: selectedIndex.value + 1 }));
const alternativeProfiles = computed(() =>
    alternativeLines.value.map(a => ({
        stageId: a.id,
        color: variantColor(a.index),
        label: t("journeyDay.variant", { n: a.index + 1 }),
    })),
);

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
// Renewing on the server, not just any refetch: a routine one answered with a finished job
// keeps the progress at "done" and must not flash the line in and out.
const refreshing = computed(() => {
    const status = forecastQuery.progress.value?.status;
    return forecastQuery.isFetching.value && !!status && status !== "done" && status !== "failed";
});
const refreshFailed = computed(() => !!forecastQuery.error.value && !forecastQuery.isFetching.value);
const { position, positionMinutes, selectPosition, selectMinutes } = useRoutePosition(() => forecast.value);

// The candidates the planner did not pick, grey, when the user asks for them: the wanted
// categories in the area of every variant, and other lodging where the day could have ended.
const showAllPois = ref(false);
const stageIds = computed(() => (showAllPois.value ? stages.value.map(s => s.id) : []));
const areaPois = useJourneyStagesPois(
    () => journey.value.id,
    stageIds,
    () => journey.value.poiCategories,
    true,
);

function mapPoi(poi: PoiOut, planned: boolean, note?: string): MapPoi {
    return { osmRef: poi.osmRef, lon: poi.lon, lat: poi.lat, category: poi.category, name: poi.name, planned, note };
}
const mapPois = computed<MapPoi[]>(() => {
    const planned = [
        ...(stage.value?.breaks ?? []).flatMap(b => (b.pois ?? []).map(p => mapPoi(p, true))),
        ...(stage.value?.detours ?? []).map(p => mapPoi(p, true)),
        ...(day.value.lodging ? [mapPoi(day.value.lodging, true)] : []),
    ];
    // The other variants' stops, grey like the candidates: planned, but not on the picked line.
    const otherPlanned = stages.value.flatMap((s, index) =>
        s.id === selectedId.value
            ? []
            : [
                  ...(s.breaks ?? []).flatMap(b =>
                      (b.pois ?? []).map(p => mapPoi(p, false, t("journeyDay.breakIn", { n: index + 1 }))),
                  ),
                  ...(s.detours ?? []).map(p => mapPoi(p, false, t("journeyDay.detourIn", { n: index + 1 }))),
              ],
    );
    const candidates = areaPois.value.flatMap(query => (query.data ?? []).map(p => mapPoi(p, false)));
    // One marker per OSM object, planned first: a machine selling drinks and sweets comes back
    // once per category, and variants share most of their area.
    const seen = new Set<string>();
    return [...planned, ...otherPlanned, ...candidates].filter(p => !seen.has(p.osmRef) && seen.add(p.osmRef));
});

const missingGaps = computed(() =>
    Object.entries(stage.value?.gaps ?? {}).flatMap(([category, gap]) => {
        if (!journey.value.poiCategories.includes(category)) return [];
        const label = gapExcessLabel(gap, stage.value?.legSeconds, stage.value?.legM ?? journey.value.maxLegDistanceM);
        return label ? [`${poiCategory(category).label}: ${label}`] : [];
    }),
);
const planningWarnings = computed(() =>
    (stage.value?.reasons ?? []).filter(isPlanningWarning).map(reasonText),
);

const detailReasonsData = computed(() => (stage.value?.reasons ?? []).filter(reason => !isPlanningWarning(reason)));
const detailReasons = computed(() => detailReasonsData.value.map(reasonText).filter(Boolean));
const fallbackDetours = computed(() =>
    detailReasonsData.value.some(reason => reason.kind === JourneyReasonOutKindEnum.Detour) ? [] : (stage.value?.detours ?? []),
);

function breakEta(elapsedS: number): string {
    const departure = stage.value?.recommendedDeparture ?? stage.value?.departureTime;
    if (!departure) return duration(elapsedS);
    const start = new Date(departure).getTime();
    return new Date(start + elapsedS * 1000).toLocaleTimeString(intlLocale(), {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Europe/Zurich",
    });
}
</script>

<template>
    <div class="journey-day">
        <div class="journey-overview" :class="{ 'has-forecast': forecast }">
            <q-card class="q-pa-sm" data-testid="journey-selector">
                <div class="row items-center q-gutter-x-sm q-mb-sm">
                    <span class="text-subtitle2">
                        {{ journey.kind === "random" ? "" : `${t("journeyDay.day", { n: day.index + 1 })} · ` }}{{
                            dayLabel(day.date)
                        }}
                    </span>
                    <span v-if="day.weatherRouted" class="text-caption text-muted">{{ t("journeyDay.weatherRouted") }}</span>
                </div>
                <div v-if="stages.length > 1" class="variant-grid" role="group" :aria-label="t('journeyDay.pickRoute')">
                    <button
                        v-for="(alternative, n) in stages"
                        :key="alternative.id"
                        type="button"
                        class="variant-button"
                        :class="{ 'bg-tint-wet': alternative.id === selectedId }"
                        :aria-pressed="alternative.id === selectedId"
                        @click="pick(alternative.id)"
                    >
                        <span class="row items-center q-gutter-x-xs">
                            <span class="line-swatch" :style="{ background: variantColor(n) }" aria-hidden="true" />
                            <span>{{ t("journeyDay.variant", { n: n + 1 }) }}</span>
                            <q-icon
                                v-if="alternative.recommended"
                                :name="symSharpStar"
                                color="accent"
                                size="xs"
                                role="img"
                                :aria-label="t('departures.recommendedBadge')"
                            />
                            <span v-if="alternative.forecastStatus === 'failed'" class="text-caption text-negative">
                                {{ t("journeyDay.weatherFailed") }}
                            </span>
                            <q-spinner-dots
                                v-else-if="alternative.forecastStatus && alternative.forecastStatus !== 'done'"
                                size="1rem"
                                :aria-label="t('journeyDay.weatherLoading')"
                            />
                        </span>
                        <span class="text-caption">
                            {{ km(alternative.distanceM) }} · {{ duration(alternative.totalSeconds) }}
                            <span v-if="alternative.rideLabel">
                                · {{ rideLabelText(alternative.rideLabel, alternative.rideCause) }}
                            </span>
                        </span>
                    </button>
                </div>
                <div v-else-if="stage" class="text-body2">
                    {{ km(stage.distanceM) }} · {{ duration(stage.totalSeconds) }}
                    <span v-if="stage.rideLabel">· {{ rideLabelText(stage.rideLabel, stage.rideCause) }}</span>
                </div>
                <div v-if="stage" class="text-caption q-mt-sm" data-testid="selected-stage-summary">
                    <template v-if="stage.recommendedDeparture || stage.departureTime">
                        {{
                            stage.recommendedDeparture
                                ? t("journeyDay.recommendedDeparture", {
                                      time: clock(stage.recommendedDeparture),
                                  })
                                : t("journeyDay.departure", { time: clock(stage.departureTime!) })
                        }}
                        ·
                    </template>
                    {{ t("journeyDay.stops", stage.breaks?.length ?? 0) }}
                </div>
                <div v-if="day.lodging" class="text-caption q-mt-sm">
                    <q-icon :name="symSharpBed" />
                    {{ t("journeyDay.lodging", { name: poiName(day.lodging), offset: km(day.lodging.offsetM) }) }}
                </div>
                <q-banner v-else-if="day.lodgingMissing" dense rounded class="bg-tint-warn q-mt-sm">
                    {{ t("journeyDay.noLodging") }}
                </q-banner>
                <q-banner
                    v-if="planningWarnings.length || missingGaps.length"
                    dense
                    rounded
                    class="bg-tint-wet q-mt-sm"
                    role="alert"
                    data-testid="journey-limit-warnings"
                >
                    <template #avatar><q-icon :name="symSharpWarning" /></template>
                    <div v-for="warning in [...planningWarnings, ...missingGaps]" :key="warning">{{ warning }}</div>
                </q-banner>
                <q-expansion-item
                    v-if="stage"
                    v-model="detailsOpen"
                    dense
                    :label="t('journeyDay.stopsAndDetours')"
                    class="q-mt-xs"
                    data-testid="journey-details"
                >
                    <div class="q-pa-sm">
                        <div v-if="!stage.breaks?.length" class="text-caption text-muted">{{ t("journeyDay.noBreak") }}</div>
                        <q-timeline v-else dense layout="dense" color="primary" class="q-my-none">
                            <q-timeline-entry
                                v-for="stop in stage.breaks"
                                :key="stop.alongM"
                                :subtitle="`${km(stop.alongM)} · ${breakEta(stop.elapsedS)}`"
                            >
                                <div v-if="!stop.pois?.length" class="text-caption text-muted">
                                    {{ t("journeyDay.nothingWanted") }}
                                </div>
                                <div
                                    v-for="poi in stop.pois"
                                    :key="`${poi.osmRef}:${poi.category}`"
                                    class="text-caption"
                                >
                                    {{ poiCategory(poi.category).emoji }} {{ poiName(poi) }}
                                </div>
                            </q-timeline-entry>
                        </q-timeline>
                        <div v-for="detour in fallbackDetours" :key="detour.osmRef" class="text-caption text-muted">
                            {{ t("journeyDay.detourTo", { poi: `${poiCategory(detour.category).emoji} ${poiName(detour)}` }) }}
                        </div>
                        <div v-for="reason in detailReasons" :key="reason" class="text-caption text-muted">
                            {{ reason }}
                        </div>
                    </div>
                </q-expansion-item>
            </q-card>

            <q-card v-if="forecast">
                <q-card-section v-if="refreshing || refreshFailed" class="q-pb-none">
                    <ForecastFreshness
                        :computed-at="forecast.computedAt"
                        :refreshing="refreshing"
                        :failed="refreshFailed"
                        :percent="progressPercent"
                    />
                </q-card-section>
                <ForecastSummaryCard flat :forecast="forecast" />
            </q-card>
            <q-banner v-else-if="!day.forecastAvailable" rounded class="bg-tint-neutral">
                <template #avatar><q-icon :name="symSharpCloudOff" class="text-muted" /></template>
                <template v-if="journey.kind === 'random'">{{ t("journeyDay.noForecastRandom") }}</template>
                <template v-else>{{ t("journeyDay.noForecastJourney") }}</template>
            </q-banner>
            <q-banner v-else-if="forecastQuery.error.value" rounded class="bg-tint-error">
                {{ t("journeyDay.weatherLoadFailed") }}
            </q-banner>
        </div>
        <div v-if="stage || forecast" class="journey-charts" data-testid="journey-charts">
            <ElevationChart
                v-if="stage"
                :stage-id="stage.id"
                :color="selectedProfileColor"
                :label="selectedLabel"
                :alternatives="alternativeProfiles"
                :position="position"
                compact
                @select-position="selectPosition"
            >
                <template v-if="alternativeProfiles.length" #footer>
                    <span
                        class="line-swatch q-mr-xs"
                        :style="{ background: selectedProfileColor }"
                        aria-hidden="true"
                    />
                    {{ t("journeyDay.chosen", { label: selectedLabel }) }} ·
                    <span
                        v-for="alternative in alternativeProfiles"
                        :key="alternative.stageId"
                        class="line-swatch q-mr-xs"
                        :style="{ background: alternative.color }"
                        aria-hidden="true"
                    />
                    {{ t("journeyDay.otherVariants") }}
                </template>
            </ElevationChart>
            <template v-if="forecast">
                <q-card v-for="kind in ['headwind', 'temperature'] as const" :key="kind" class="weather-chart-card">
                    <WeatherChart
                        :kind="kind"
                        :version="forecast.version"
                        :cursor-minutes="positionMinutes"
                        :samples="forecast.samples"
                        @select-minutes="selectMinutes"
                    />
                </q-card>
            </template>
        </div>

        <!-- Without a forecast (past the forecast window) the map still shows the line and the stops. -->
        <q-card v-if="stage" class="column overflow-hidden" style="height: 55vh; min-height: 320px">
            <NiceMap
                :route-weather="forecast"
                :preview-line="forecast ? undefined : stage.path"
                :position="position"
                :pois="mapPois"
                :alternative-lines="alternativeLines"
                @select-position="selectPosition"
                @select-alternative="pick"
            />
        </q-card>
        <div v-if="stage" class="row items-center">
            <q-toggle
                v-model="showAllPois"
                dense
                :label="t('journeyDay.showMorePois')"
                size="sm"
                class="no-padding"
            />
            <span class="text-caption text-muted">
                {{ t("journeyDay.coloured") }} ·
                <span class="poi-dot" :style="{ background: CANDIDATE_COLOR }" aria-hidden="true" />
                {{ t("map.notPlanned") }}
            </span>
            <span v-if="alternativeLines.length" class="text-caption text-muted">
                <span
                    v-for="alternative in alternativeLines"
                    :key="alternative.id"
                    class="line-swatch q-mr-xs"
                    :style="{ background: variantColor(alternative.index) }"
                    aria-hidden="true"
                />
                {{ t("journeyDay.tapToPick") }}
            </span>
        </div>

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
            <div class="text-muted q-mt-sm">{{ t("forecast.loading") }}</div>
        </q-inner-loading>
    </div>
</template>

<style scoped>
.journey-day {
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-width: 0;
}
.journey-day > * {
    min-width: 0;
}
.journey-overview {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
    align-items: start;
}
.journey-overview > * {
    min-width: 0;
}
@media (min-width: 1024px) {
    .journey-overview.has-forecast {
        grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
    }
}
.variant-grid,
.journey-charts {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
}
.variant-grid {
    gap: 8px;
}
.variant-button {
    font: inherit;
    color: inherit;
    background: transparent;
    border: 1px solid var(--q-primary);
    border-radius: 6px;
    padding: 8px 12px;
    text-align: left;
    cursor: pointer;
    min-width: 0;
}
.variant-button[aria-pressed="true"] {
    box-shadow: inset 0 0 0 1px var(--q-primary);
}
.variant-button:focus-visible {
    outline: 3px solid var(--q-primary);
    outline-offset: 2px;
}
.weather-chart-card {
    min-height: 280px;
    position: relative;
    min-width: 0;
}
.weather-chart-card > :deep(.fit) {
    position: absolute;
    inset: 0;
}
@media (max-width: 599px) {
    .variant-grid,
    .journey-charts {
        grid-template-columns: minmax(0, 1fr);
    }
}
.quality-swatch {
    display: inline-block;
    width: 14px;
    height: 14px;
    border-radius: 3px;
}
.line-swatch {
    display: inline-block;
    width: 18px;
    height: 4px;
    border-radius: 2px;
    vertical-align: middle;
}
.poi-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    vertical-align: middle;
}
</style>
