<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useQueryClient } from "@tanstack/vue-query";
import { useNow } from "@vueuse/core";
import { useI18n } from "vue-i18n";
import { ResponseError } from "@norain/api/runtime";
import {
    CoreApiSystemMapFeaturesLayerEnum as Layer,
    CoreApiSystemMapFeaturesKindEnum as Kind,
    CoreApiSystemMapFeaturesSourceEnum as Source,
    CoreApiSystemMapFeaturesProfileEnum as Profile,
} from "@norain/api/apis";
import type { SystemFeature, SystemJob } from "@norain/api/models";
import SystemMap from "@/components/SystemMap.vue";
import { useSession } from "@/composables/useSession";
import { useBackendHost } from "@/utils";
import { intlLocale } from "@/i18n";
import { jobErrorText } from "@/utils/serverErrors";
import {
    CACHE_COLORS,
    COVERAGE_COLORS,
    browserStatTotals,
    cacheFreshness,
    coverageLabels,
    ELEVATION_COLORS,
    ROAD_COVERAGE_COLOR,
    elevationLabels,
    statsUnder,
} from "@/utils/systemOverview";
import { areaName } from "@/utils/coverage";
import {
    systemKey,
    useSystemSummary,
    useSystemLayer,
    useSystemCoverage,
    useSystemCellHistory,
    useSystemJobs,
    useSystemEvents,
    useSystemBrowser,
    useSystemBrowserStats,
    useSystemDataCoverage,
} from "@/queries/system";
import { recheckRecognition } from "@/services/browserRecognition";
import { symSharpRefresh } from "@quasar/extras/material-symbols-sharp";

definePage({ meta: { requiresAuth: true, requiresSystem: true, titleKey: "pages.system" } });
const { t } = useI18n();
const { session, refreshSession } = useSession();
const allowed = computed(() => session.value.system?.allowed === true);
const queryClient = useQueryClient();
const viewport = ref<{ bbox: string; zoom: number } | null>(null);
const showRoutes = ref(true),
    showJourneys = ref(true),
    showCells = ref(true),
    alternatives = ref(false),
    showRoadCoverage = ref(false),
    showElevation = ref(false);
const kind = ref(Kind.Forecast),
    source = ref(Source.All),
    profile = ref<Profile | null>(null);
const active = ref<boolean | null>(null),
    day = ref("");
const selected = ref<SystemFeature | null>(null);
const historyOffset = ref(0),
    jobsOffset = ref(0),
    mapError = ref("");
const now = useNow({ interval: 15_000 });
const summary = useSystemSummary(allowed);
const baseParams = computed(() => ({
    bbox: viewport.value?.bbox,
    zoom: viewport.value?.zoom,
    profile: profile.value ?? undefined,
}));
const routes = useSystemLayer(
    () => ({ ...baseParams.value, layer: Layer.Routes, active: active.value }),
    () => allowed.value && showRoutes.value && !!viewport.value,
);
const journeys = useSystemLayer(
    () => ({ ...baseParams.value, layer: Layer.Journeys, alternatives: alternatives.value }),
    () => allowed.value && showJourneys.value && !!viewport.value,
);
const cells = useSystemLayer(
    () => ({
        ...baseParams.value,
        layer: Layer.Cells,
        kind: kind.value,
        source: source.value,
        day: day.value ? new Date(`${day.value}T00:00:00Z`) : undefined,
    }),
    () => allowed.value && showCells.value && !!viewport.value,
);
const coverage = useSystemCoverage(selected, allowed);
const history = useSystemCellHistory(selected, historyOffset, allowed);
const jobs = useSystemJobs(jobsOffset, allowed);
const browser = useSystemBrowser(allowed);
const browserStats = useSystemBrowserStats(allowed);
const dataCoverage = useSystemDataCoverage(allowed);
const graphCoverage = computed(() => dataCoverage.data.value?.graph ?? null);
const terrainCoverage = computed(() => dataCoverage.data.value?.terrain ?? null);
const photonCoverage = computed(() => dataCoverage.data.value?.photon ?? null);
const photonCountries = computed(() =>
    (photonCoverage.value?.countries ?? [])
        .map(country => `${areaName(country.code)} (${number(country.places)})`)
        .join(" · "),
);
const statTotals = computed(() => browserStatTotals(browserStats.data.value?.days ?? []));
const checkingBrowser = ref(false);
const { live } = useSystemEvents(allowed);
const allFeatures = computed(() => [...routes.items.value, ...journeys.items.value, ...cells.items.value]);
const queries = [
    summary,
    routes.query,
    journeys.query,
    cells.query,
    coverage,
    history,
    jobs,
    browser,
    browserStats,
    dataCoverage,
];
const failed = computed(() => queries.some(query => query.isError.value));
const refreshing = computed(() => queries.some(query => query.isFetching.value));
const mapLoading = computed(
    () => routes.query.isFetching.value || journeys.query.isFetching.value || cells.query.isFetching.value,
);
const loaded = computed(() => allFeatures.value.length);
const total = computed(() => routes.total.value + journeys.total.value + cells.total.value);
const maxAge = computed(() => summary.data.value?.maxCellAgeSeconds ?? 7200);
const coverageCounts = computed(() =>
    Object.entries(coverageLabels()).map(([status, label]) => ({
        status,
        label,
        forecast: countCoverage(status, "forecast"),
        ensemble: countCoverage(status, "ensemble"),
    })),
);
function countCoverage(status: string, field: "forecast" | "ensemble") {
    const states: string[] = coverage.data.value?.points.map(point => point[field]) ?? [];
    return states.filter(value => value === status).length;
}
const isCell = computed(() => selected.value?.kind === "forecast" || selected.value?.kind === "ensemble");
const summaryCards = computed(() =>
    summary.data.value
        ? [
              [t("system.summary.recurringRoutes"), summary.data.value.recurringRoutes],
              [t("pages.journeys"), summary.data.value.journeys],
              [t("system.summary.stages"), summary.data.value.stages],
              [t("system.summary.missingGeometry"), summary.data.value.missingGeometry],
              [t("system.summary.cacheLocations"), summary.data.value.cacheLocations],
          ]
        : [],
);
const profileOptions = computed(() => [
    { label: t("system.filter.allProfiles"), value: null },
    ...Object.values(Profile).map(value => ({ label: value, value })),
]);
const sourceOptions = computed(() => [
    { label: t("system.filter.allProviders"), value: Source.All },
    { label: "Open-Meteo", value: Source.OpenMeteo },
    { label: "MET Norway", value: Source.MetNorway },
    { label: "OpenWeatherMap", value: Source.Openweathermap },
]);
const activeOptions = computed(() => [
    { label: t("system.filter.allRoutes"), value: null },
    { label: t("system.filter.activeRoutes"), value: true },
    { label: t("system.filter.inactiveRoutes"), value: false },
]);
const kindOptions = computed(() => [
    { label: t("system.filter.forecasts"), value: Kind.Forecast },
    { label: t("system.filter.ensemble"), value: Kind.Ensemble },
]);

function dateTime(value?: Date | null) {
    return value
        ? value.toLocaleString(intlLocale(), { timeZone: "Europe/Zurich", dateStyle: "short", timeStyle: "short" })
        : "–";
}
function number(value: number) {
    return value.toLocaleString(intlLocale());
}
function age(value?: Date | null) {
    if (!value) return "–";
    const minutes = Math.max(0, Math.floor((now.value.getTime() - value.getTime()) / 60000));
    return minutes < 60
        ? t("system.age.minutes", { m: minutes })
        : t("system.age.hours", { h: Math.floor(minutes / 60), m: minutes % 60 });
}
// A stalled job writes nothing, so no change notice reports it: re-derive the flag on the page clock.
function stalled(job: SystemJob) {
    const timeout = jobs.data.value?.stallTimeoutSeconds;
    if (job.possiblyStalled) return true;
    if (timeout === undefined || ["done", "failed"].includes(job.status)) return false;
    return now.value.getTime() - job.updatedAt.getTime() > timeout * 1000;
}
const TIER_COLORS: Record<string, string> = { high: "positive", low: "warning", suspicious: "negative" };
async function checkBrowser() {
    checkingBrowser.value = true;
    try {
        await recheckRecognition();
        await browser.refetch();
    } finally {
        checkingBrowser.value = false;
    }
}
function choose(feature: SystemFeature) {
    selected.value = feature;
    historyOffset.value = 0;
}
watch(allFeatures, items => {
    const current = selected.value;
    if (!current) {
        return;
    }
    const updated = items.find(
        item =>
            item.kind === current.kind &&
            (isCell.value ? item.lat === current.lat && item.lon === current.lon : item.id === current.id),
    );
    if (updated) {
        selected.value = updated;
    }
});
async function refresh() {
    mapError.value = "";
    await refreshSession();
    if (allowed.value) {
      // await queryClient.refetchQueries({ queryKey: systemKey })
        await queryClient.invalidateQueries({ queryKey: systemKey });
    }
}
watch(failed, async () => {
    if (
        queries.some(
            query =>
                query.error.value instanceof ResponseError && [401, 403].includes(query.error.value.response.status),
        )
    )
        await refreshSession();
});
watch([showRoutes, showJourneys, showCells, kind, source, day, active, profile, alternatives], () => {
    selected.value = null;
});
onBeforeUnmount(async () => {
    await queryClient.cancelQueries({ queryKey: systemKey });
    queryClient.removeQueries({ queryKey: systemKey });
});
</script>

<template>
    <q-page class="system-page q-pa-md">
        <div class="row items-center q-gutter-sm q-mb-md">
            <div class="col">
                <h1 class="text-h5 q-my-none">{{ t("pages.system") }}</h1>
                <div class="text-caption">{{ t("system.subtitle") }}</div>
            </div>
            <q-btn
                outline
                :icon="symSharpRefresh"
                no-caps
                :label="t('system.refresh')"
                :disable="refreshing"
                @click="refresh"
            />
        </div>
        <q-banner v-if="!allowed" rounded class="bg-amber-2 text-dark">
            {{ t("system.needAdmin") }}
            <template #action>
                <q-btn
                    v-if="session.system"
                    flat
                    no-caps
                    :label="t('system.adminLogin')"
                    :href="`${useBackendHost()}${session.system.loginUrl}`"
                    target="_blank"
                    rel="noopener"
                />
            </template>
            <div class="text-caption">{{ t("system.afterLogin") }}</div>
        </q-banner>
        <template v-else>
            <q-banner v-if="failed || mapError" rounded class="bg-amber-2 text-dark q-mb-sm" role="alert">
                {{ failed ? t("system.partialFailure") : mapError }}
                <template #action><q-btn flat no-caps :label="t('common.retry')" @click="refresh" /></template>
            </q-banner>
            <div class="text-caption q-mb-sm">
                <span v-if="live">{{ t("system.live") }}</span>
                <span v-else>{{ t("system.notLive") }}</span>
                <span v-if="summary.dataUpdatedAt.value">
                    {{ t("system.lastLoaded", { time: dateTime(new Date(summary.dataUpdatedAt.value)) }) }}
                </span>
            </div>
            <div class="summary-grid q-mb-md">
                <q-card v-for="[label, value] in summaryCards" :key="String(label)" flat bordered class="q-pa-sm">
                    <div class="text-h5">{{ value }}</div>
                    <div class="text-caption">{{ label }}</div>
                </q-card>
            </div>
            <q-linear-progress v-if="summary.isPending.value" indeterminate :aria-label="t('system.loading')" />
            <div class="row q-gutter-sm items-center q-mb-sm">
                <q-checkbox v-model="showRoutes" :label="t('system.summary.recurringRoutes')" dense />
                <q-checkbox v-model="showJourneys" :label="t('pages.journeys')" dense />
                <q-checkbox
                    v-model="alternatives"
                    :label="t('system.layer.alternatives')"
                    :disable="!showJourneys"
                    dense
                />
                <q-checkbox v-model="showCells" :label="t('system.layer.cells')" dense />
                <q-checkbox
                    v-model="showRoadCoverage"
                    :label="t('system.dataCoverage.roadLayer')"
                    :disable="!dataCoverage.data.value?.roadBoxes.length"
                    dense
                />
                <q-checkbox
                    v-model="showElevation"
                    :label="t('system.dataCoverage.elevationLayer')"
                    :disable="!terrainCoverage?.cellCounts"
                    dense
                />
            </div>
            <div class="filters q-mb-sm">
                <q-select
                    v-model="profile"
                    :options="profileOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    :label="t('system.filter.profile')"
                />
                <q-select
                    v-model="active"
                    :options="activeOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    :label="t('system.summary.recurringRoutes')"
                    :disable="!showRoutes"
                />
                <q-select
                    v-model="kind"
                    :options="kindOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    :label="t('system.filter.kind')"
                />
                <q-select
                    v-model="source"
                    :options="sourceOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    :label="t('system.filter.provider')"
                    :disable="kind === Kind.Ensemble || !showCells"
                />
                <q-input
                    v-model="day"
                    type="date"
                    clearable
                    dense
                    outlined
                    :label="t('system.filter.cacheDate')"
                    :disable="!showCells"
                />
            </div>
            <div class="text-caption q-mb-sm">
                {{ t("system.filterHint") }}
            </div>
            <div class="row q-gutter-md text-caption q-mb-sm" :aria-label="t('system.legend.label')">
                <span>
                    <i class="legend-line" :style="{ background: CACHE_COLORS.fresh }" />
                    {{ t("system.legend.fresh", { h: maxAge / 7200 }) }}
                </span>
                <span>
                    <i class="legend-line" :style="{ background: CACHE_COLORS.aging }" />
                    {{ t("system.legend.aging", { h: maxAge / 3600 }) }}
                </span>
                <span>
                    <i class="legend-line" :style="{ background: CACHE_COLORS.stale }" />
                    {{ t("system.legend.stale", { h: maxAge / 3600 }) }}
                </span>
                <span>
                    <i class="legend-line" style="background: #2186bd" />
                    {{ t("pages.route") }}
                </span>
                <span>
                    <i class="legend-line" style="background: #9264cf" />
                    {{ t("pages.journey") }}
                </span>
                <span v-if="showRoadCoverage">
                    <i class="legend-box" :style="{ background: ROAD_COVERAGE_COLOR }" />
                    {{ t("system.dataCoverage.roadLayer") }}
                </span>
                <template v-if="showElevation">
                    <span v-for="(label, level) in elevationLabels()" :key="level">
                        <i class="legend-box" :style="{ background: ELEVATION_COLORS[level] }" />
                        {{ label }}
                    </span>
                </template>
            </div>
            <div class="overview-grid">
                <div class="map-panel">
                    <div class="text-caption q-pa-xs">
                        {{ t("system.mapLoaded", { loaded, total }) }}
                        <span v-if="mapLoading">{{ t("common.loading") }}</span>
                        <span v-else-if="!total">{{ t("system.noMatches") }}</span>
                    </div>
                    <!-- A fixed slot: the bar coming and going would resize the map, whose moveend
                         sets a new viewport, which loads again. -->
                    <div class="map-progress">
                        <q-linear-progress v-if="mapLoading" indeterminate />
                    </div>
                    <SystemMap
                        v-if="summary.data.value"
                        :items="allFeatures"
                        :bounds="summary.data.value.bounds"
                        :selected="selected"
                        :points="coverage.data.value?.points ?? []"
                        :coverage-kind="kind"
                        :now="now.getTime()"
                        :max-age-seconds="maxAge"
                        :road-boxes="dataCoverage.data.value?.roadBoxes ?? []"
                        :elevation-boxes="dataCoverage.data.value?.elevationBoxes"
                        :show-roads="showRoadCoverage"
                        :show-elevation="showElevation"
                        @viewport="viewport = $event"
                        @select="choose"
                        @error="mapError = $event"
                    />
                    <div class="text-caption q-pa-xs">
                        {{ t("system.gridHint") }}
                    </div>
                </div>
                <aside class="diagnostics" :aria-label="t('system.diagnostics')">
                    <q-card flat bordered class="q-mb-sm">
                        <q-card-section class="q-pa-sm">
                            <div class="row items-center">
                                <h2 class="text-subtitle1 col q-my-none">
                                    {{ selected ? (isCell ? t("system.cell") : selected.name) : t("system.selection") }}
                                </h2>
                                <q-btn
                                    v-if="selected"
                                    flat
                                    dense
                                    :label="t('common.close')"
                                    no-caps
                                    @click="selected = null"
                                />
                            </div>
                            <p v-if="!selected" class="text-caption q-mb-none">
                                {{ t("system.selectionHint") }}
                            </p>
                            <template v-else-if="isCell">
                                <div class="text-caption">
                                    {{ selected.lat?.toFixed(2) }}, {{ selected.lon?.toFixed(2) }} ·
                                    {{ selected.source }}
                                </div>
                                <div class="text-caption">
                                    {{
                                        t("system.fetched", {
                                            time: dateTime(selected.fetchedAt),
                                            age: age(selected.fetchedAt),
                                        })
                                    }}
                                </div>
                                <div class="text-caption">
                                    {{ t("system.horizon", { days: selected.forecastDays }) }}
                                </div>
                                <q-linear-progress v-if="history.isFetching.value" indeterminate />
                                <q-list dense separator>
                                    <q-item
                                        v-for="record in history.data.value?.items"
                                        :key="record.id"
                                        class="q-px-none"
                                    >
                                        <q-item-section>
                                            <q-item-label>{{ record.source }}</q-item-label>
                                            <q-item-label caption>
                                                {{ dateTime(record.fetchedAt) }} ·
                                                {{ t("system.days", { n: record.forecastDays }) }}
                                                <br />
                                                {{
                                                    t("system.cacheDay", {
                                                        day: record.dayKey?.toISOString().slice(0, 10),
                                                    })
                                                }}
                                            </q-item-label>
                                        </q-item-section>
                                        <q-item-section side>
                                            <span
                                                :style="{
                                                    color: CACHE_COLORS[
                                                        cacheFreshness(record.fetchedAt!, now.getTime(), maxAge)
                                                    ],
                                                }"
                                            >
                                                {{ age(record.fetchedAt) }}
                                            </span>
                                        </q-item-section>
                                    </q-item>
                                </q-list>
                                <div class="row items-center justify-between">
                                    <q-btn
                                        flat
                                        dense
                                        no-caps
                                        :label="t('common.back')"
                                        :disable="!historyOffset"
                                        @click="historyOffset = Math.max(0, historyOffset - 25)"
                                    />
                                    <span class="text-caption">
                                        {{ t("system.entries", { n: history.data.value?.total ?? 0 }) }}
                                    </span>
                                    <q-btn
                                        flat
                                        dense
                                        no-caps
                                        :label="t('system.next')"
                                        :disable="history.data.value?.nextOffset == null"
                                        @click="historyOffset = history.data.value!.nextOffset!"
                                    />
                                </div>
                            </template>
                            <template v-else>
                                <q-linear-progress v-if="coverage.isFetching.value" indeterminate />
                                <template v-if="coverage.data.value">
                                    <div class="text-caption">
                                        {{ coverage.data.value.profile }} ·
                                        {{ ((coverage.data.value.distanceM ?? 0) / 1000).toFixed(1) }} km ·
                                        {{
                                            t("system.age.minutes", {
                                                m: Math.round((coverage.data.value.durationSeconds ?? 0) / 60),
                                            })
                                        }}
                                    </div>
                                    <div class="text-caption">
                                        {{
                                            t("system.geometry", {
                                                time: dateTime(coverage.data.value.geometryFetchedAt),
                                            })
                                        }}
                                    </div>
                                    <div class="text-caption">
                                        {{ t("system.departure", { time: dateTime(coverage.data.value.departure) }) }}
                                    </div>
                                    <p v-if="coverage.data.value.unavailable" class="q-mt-sm q-mb-none">
                                        {{ coverage.data.value.unavailable }}
                                    </p>
                                    <template v-else>
                                        <div class="text-caption q-mt-sm">
                                            {{ t("system.fixedDeparture") }}
                                        </div>
                                        <table class="diagnostic-table">
                                            <thead>
                                                <tr>
                                                    <th>{{ t("system.table.status") }}</th>
                                                    <th>{{ t("system.table.forecast") }}</th>
                                                    <th>{{ t("system.filter.ensemble") }}</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                <tr v-for="count in coverageCounts" :key="count.status">
                                                    <td>
                                                        <i
                                                            class="legend-dot"
                                                            :style="{
                                                                background:
                                                                    COVERAGE_COLORS[
                                                                        count.status as keyof typeof COVERAGE_COLORS
                                                                    ],
                                                            }"
                                                        />
                                                        {{ count.label }}
                                                    </td>
                                                    <td>{{ count.forecast }}</td>
                                                    <td>{{ count.ensemble }}</td>
                                                </tr>
                                            </tbody>
                                        </table>
                                        <div class="text-caption">
                                            {{
                                                t("system.pointsHint", {
                                                    kind:
                                                        kind === Kind.Forecast
                                                            ? t("system.table.forecast")
                                                            : t("system.filter.ensemble"),
                                                })
                                            }}
                                        </div>
                                    </template>
                                </template>
                            </template>
                        </q-card-section>
                    </q-card>
                    <q-expansion-item :label="t('system.cacheTotal')" default-opened class="bordered-panel q-mb-sm">
                        <div class="q-pa-sm">
                            <div v-if="!summary.data.value?.caches.length" class="text-caption">
                                {{ t("system.noCells") }}
                            </div>
                            <div
                                v-for="cache in summary.data.value?.caches"
                                :key="cache.kind + cache.source"
                                class="q-mb-sm"
                            >
                                <div class="text-body2">{{ cache.source }}</div>
                                <div class="text-caption">
                                    {{ t("system.locations", { n: cache.locations }) }} ·
                                    {{ t("system.entries", { n: cache.records }) }}
                                </div>
                                <div class="text-caption">
                                    {{ t("system.freshStale", { fresh: cache.fresh, stale: cache.stale }) }}
                                </div>
                            </div>
                            <div class="text-caption">
                                {{ t("system.locationHint") }}
                            </div>
                        </div>
                    </q-expansion-item>
                    <q-expansion-item :label="t('system.dataCoverage.title')" class="bordered-panel q-mb-sm">
                        <div class="q-pa-sm text-caption">
                            <q-linear-progress v-if="dataCoverage.isFetching.value" indeterminate />
                            <div class="text-weight-medium">{{ t("system.dataCoverage.graph") }}</div>
                            <template v-if="graphCoverage">
                                <div>
                                    {{
                                        t("system.dataCoverage.release", {
                                            release: graphCoverage.release,
                                            status: graphCoverage.status,
                                        })
                                    }}
                                </div>
                                <div>
                                    {{ t("system.dataCoverage.built", { time: dateTime(graphCoverage.builtAt) }) }}
                                </div>
                                <div>
                                    {{
                                        t("system.dataCoverage.osmFile", {
                                            file: graphCoverage.osmFile ?? "–",
                                            time: dateTime(graphCoverage.osmFileModified),
                                        })
                                    }}
                                </div>
                                <div>{{ t("system.dataCoverage.roadCells", { n: number(graphCoverage.cells) }) }}</div>
                                <div v-if="graphCoverage.cellsSource === 'terrain'" class="text-warning">
                                    {{ t("system.dataCoverage.cellsFromTerrain") }}
                                </div>
                                <div v-else-if="graphCoverage.cellsSource === 'bounds'" class="text-warning">
                                    {{ t("system.dataCoverage.cellsFromBounds") }}
                                </div>
                            </template>
                            <div v-else-if="dataCoverage.data.value" class="text-negative">
                                {{ t("system.dataCoverage.graphUnavailable") }}
                            </div>
                            <div class="text-weight-medium q-mt-sm">{{ t("system.dataCoverage.terrain") }}</div>
                            <template v-if="terrainCoverage">
                                <div>
                                    {{
                                        t("system.dataCoverage.terrainKey", {
                                            key: terrainCoverage.key,
                                            version: terrainCoverage.catalogVersion,
                                        })
                                    }}
                                </div>
                                <div>
                                    {{ t("system.dataCoverage.sources", { zoom: terrainCoverage.zoom ?? "–" }) }}:
                                    {{ terrainCoverage.sources.map(source => source.name).join(", ") || "–" }}
                                </div>
                                <div>
                                    {{
                                        t("system.dataCoverage.fallbackSource", {
                                            zoom: terrainCoverage.fallbackZoom ?? "–",
                                            name: terrainCoverage.fallbackSource ?? "–",
                                        })
                                    }}
                                </div>
                                <template v-if="terrainCoverage.cellCounts">
                                    <div v-for="(label, level) in elevationLabels()" :key="level">
                                        <i class="legend-box" :style="{ background: ELEVATION_COLORS[level] }" />
                                        {{ label }}: {{ number(terrainCoverage.cellCounts[level]) }}
                                    </div>
                                </template>
                                <div v-else class="text-warning">{{ t("system.dataCoverage.noCellCoverage") }}</div>
                                <div v-for="check in terrainCoverage.checks" :key="check.zoom ?? -1">
                                    {{
                                        check.decoded
                                            ? t("system.dataCoverage.checkDecoded", {
                                                  zoom: check.zoom ?? "–",
                                                  positions: number(check.positions),
                                                  missing: number(check.missing ?? 0),
                                                  nodata: number(check.nodata ?? 0),
                                              })
                                            : t("system.dataCoverage.checkStructure", {
                                                  zoom: check.zoom ?? "–",
                                                  positions: number(check.positions),
                                              })
                                    }}
                                </div>
                            </template>
                            <div v-else-if="dataCoverage.data.value" class="text-negative">
                                {{ t("system.dataCoverage.terrainUnavailable") }}
                            </div>
                            <div class="text-weight-medium q-mt-sm">{{ t("system.dataCoverage.photon") }}</div>
                            <template v-if="photonCoverage">
                                <div v-if="!photonCoverage.reachable" class="text-negative">
                                    {{ t("system.dataCoverage.photonUnreachable") }}
                                </div>
                                <div v-else>
                                    {{
                                        t("system.dataCoverage.photonIndex", {
                                            time: dateTime(photonCoverage.importDate),
                                            version: photonCoverage.version ?? "–",
                                        })
                                    }}
                                </div>
                                <template v-if="photonCoverage.manifest">
                                    <div>
                                        {{ t("system.dataCoverage.photonSources") }}:
                                        {{ photonCoverage.sources?.join(", ") || "–" }}
                                    </div>
                                    <div>
                                        {{ t("system.dataCoverage.photonCountries") }}:
                                        {{ photonCountries || t("system.dataCoverage.photonPrebuilt") }}
                                    </div>
                                </template>
                                <div v-else class="text-warning">{{ t("system.dataCoverage.noPhotonFile") }}</div>
                            </template>
                            <div v-else-if="dataCoverage.data.value" class="text-negative">
                                {{ t("system.dataCoverage.photonUnavailable") }}
                            </div>
                        </div>
                    </q-expansion-item>
                    <q-expansion-item :label="t('system.jobs.title')" default-opened class="bordered-panel">
                        <div class="q-pa-sm text-caption">
                            {{ t("system.jobs.hint") }}
                        </div>
                        <q-linear-progress v-if="jobs.isFetching.value" indeterminate />
                        <div v-if="jobs.data.value && !jobs.data.value.items.length" class="q-pa-sm text-caption">
                            {{ t("system.jobs.none") }}
                        </div>
                        <q-list dense separator>
                            <q-item v-for="job in jobs.data.value?.items" :key="job.id">
                                <q-item-section>
                                    <q-item-label>{{ job.kind }} · {{ job.status }}</q-item-label>
                                    <q-item-label v-if="stalled(job)" class="text-negative">
                                        {{ t("system.jobs.stalled") }}
                                    </q-item-label>
                                    <q-item-label caption>
                                        {{
                                            t("system.jobs.cells", {
                                                settled: job.cellsSettled,
                                                total: job.cellsTotal,
                                                failed: job.cellsFailed,
                                            })
                                        }}
                                    </q-item-label>
                                    <q-item-label caption>
                                        {{ t("system.jobs.created", { time: dateTime(job.createdAt) }) }}
                                        <br />
                                        {{ t("system.jobs.updated", { time: dateTime(job.updatedAt) }) }}
                                    </q-item-label>
                                    <q-item-label v-if="job.error" caption class="job-error">
                                        {{ jobErrorText(job.error) }}
                                    </q-item-label>
                                </q-item-section>
                            </q-item>
                        </q-list>
                        <div class="row items-center justify-between q-pa-xs">
                            <q-btn
                                flat
                                dense
                                no-caps
                                :label="t('common.back')"
                                :disable="!jobsOffset"
                                @click="jobsOffset = Math.max(0, jobsOffset - 25)"
                            />
                            <span class="text-caption">
                                {{ t("system.jobs.count", { n: jobs.data.value?.total ?? 0 }) }}
                            </span>
                            <q-btn
                                flat
                                dense
                                no-caps
                                :label="t('system.next')"
                                :disable="jobs.data.value?.nextOffset == null"
                                @click="jobsOffset = jobs.data.value!.nextOffset!"
                            />
                        </div>
                    </q-expansion-item>
                    <q-expansion-item :label="t('system.browser.title')" class="bordered-panel">
                        <div class="q-pa-sm text-caption">{{ t("system.browser.hint") }}</div>
                        <div class="q-pa-sm">
                            <div v-if="browser.data.value && !browser.data.value.enabled" class="text-caption">
                                {{ t("system.browser.disabled") }}
                            </div>
                            <template v-else-if="browser.data.value?.assessment">
                                <q-chip
                                    dense
                                    square
                                    :color="TIER_COLORS[browser.data.value.assessment.tier]"
                                    text-color="white"
                                    :label="browser.data.value.assessment.tier"
                                />
                                <div class="text-caption q-mt-xs">
                                    {{ t("system.browser.indicators") }}:
                                    {{ browser.data.value.assessment.indicators.join(", ") || "–" }}
                                </div>
                                <div class="text-caption">
                                    {{
                                        t("system.browser.ids", {
                                            browser: browser.data.value.assessment.browserId,
                                            fingerprint: browser.data.value.assessment.fingerprintId ?? "–",
                                        })
                                    }}
                                </div>
                                <div class="text-caption">
                                    {{
                                        t("system.browser.continuity", {
                                            persistent: browser.data.value.assessment.persistent ? "✓" : "✗",
                                            established: browser.data.value.assessment.established ? "✓" : "✗",
                                            continuity: browser.data.value.assessment.continuity ? "✓" : "✗",
                                            similarity: browser.data.value.assessment.similarity ?? "–",
                                        })
                                    }}
                                </div>
                                <div class="text-caption">
                                    {{ t("system.browser.keys") }}:
                                    {{ browser.data.value.assessment.keys.join(" · ") || "–" }}
                                </div>
                                <div v-if="browser.data.value.assessment.path" class="text-caption">
                                    {{
                                        t("system.browser.path", {
                                            path: browser.data.value.assessment.path,
                                            transport: browser.data.value.assessment.pathTransport ?? "–",
                                            excess: browser.data.value.assessment.pathExcess ?? "–",
                                        })
                                    }}
                                </div>
                                <div class="text-caption">
                                    {{ t("system.browser.components") }}:
                                    {{
                                        Object.entries(browser.data.value.assessment.components)
                                            .map(([name, value]) => `${name} ${value}`)
                                            .join(" · ") || "–"
                                    }}
                                </div>
                            </template>
                            <div v-else-if="browser.data.value" class="text-caption">
                                {{ t("system.browser.none") }}
                            </div>
                            <div v-if="browser.data.value" class="text-caption">
                                {{ t("system.browser.pow", { bits: browser.data.value.powBits }) }}
                            </div>
                            <q-btn
                                class="q-mt-sm"
                                outline
                                dense
                                no-caps
                                :label="t('system.browser.check')"
                                :loading="checkingBrowser"
                                :disable="browser.data.value?.enabled === false"
                                @click="checkBrowser"
                            />
                        </div>
                    </q-expansion-item>
                    <q-expansion-item :label="t('system.browser.stats.title')" class="bordered-panel">
                        <div class="q-pa-sm text-caption">{{ t("system.browser.stats.hint") }}</div>
                        <div v-if="browserStats.data.value" class="q-pa-sm text-caption">
                            <div class="text-weight-medium">{{ t("system.browser.stats.observed") }}</div>
                            <div v-for="name in browserStats.data.value.observed" :key="name">
                                {{
                                    t("system.browser.stats.observedRow", {
                                        name,
                                        total: statTotals[`ind:${name}`] ?? 0,
                                        solo: statTotals[`solo:${name}`] ?? 0,
                                    })
                                }}
                            </div>
                            <div v-if="!browserStats.data.value.observed.length">–</div>
                            <div class="q-mt-sm">
                                {{ t("system.browser.stats.tiers") }}:
                                {{
                                    statsUnder(statTotals, "tier:")
                                        .map(([name, count]) => `${name} ${count}`)
                                        .join(" · ") || "–"
                                }}
                            </div>
                            <div>
                                {{ t("system.browser.stats.refused") }}:
                                {{
                                    statsUnder(statTotals, "refused:")
                                        .map(([name, count]) => `${name} ${count}`)
                                        .join(" · ") || "–"
                                }}
                            </div>
                            <div>
                                {{ t("system.browser.stats.pow") }}:
                                {{
                                    statsUnder(statTotals, "pow:")
                                        .map(([bits, count]) => `${bits}: ${count}`)
                                        .join(" · ") || "–"
                                }}
                            </div>
                            <div>
                                {{ t("system.browser.stats.paths") }}:
                                {{
                                    statsUnder(statTotals, "path:")
                                        .map(([name, count]) => `${name} ${count}`)
                                        .join(" · ") || "–"
                                }}
                            </div>
                            <div v-for="transport in ['tcp', 'quic']" :key="transport">
                                {{ t("system.browser.stats.excess", { transport }) }}:
                                {{
                                    statsUnder(statTotals, `excess:${transport}:`)
                                        .map(([bucket, count]) => `${bucket}: ${count}`)
                                        .join(" · ") || "–"
                                }}
                            </div>
                            <div v-if="statTotals.ip_class_e" class="text-negative">
                                {{ t("system.browser.stats.classE", { n: statTotals.ip_class_e }) }}
                            </div>
                        </div>
                    </q-expansion-item>
                </aside>
            </div>
        </template>
    </q-page>
</template>

<style scoped>
.system-page {
    max-width: 1800px;
    margin: auto;
}
.summary-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 8px;
}
.filters {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 8px;
}
.overview-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 350px;
    gap: 12px;
}
.map-panel {
    display: flex;
    flex-direction: column;
    height: max(520px, calc(100vh - 380px));
    min-width: 0;
    overflow: hidden;
    border: 1px solid #8885;
    border-radius: 8px;
}
.map-progress {
    flex: none;
    height: 4px;
}
.map-panel :deep(.system-map) {
    flex: 1;
    min-height: 0;
}
.diagnostics {
    min-width: 0;
    max-height: max(520px, calc(100vh - 380px));
    overflow: auto;
}
.bordered-panel {
    border: 1px solid #8885;
    border-radius: 8px;
}
.legend-line {
    display: inline-block;
    width: 20px;
    height: 3px;
    margin-right: 5px;
    vertical-align: middle;
}
.legend-box {
    display: inline-block;
    width: 10px;
    height: 10px;
    margin-right: 5px;
    vertical-align: middle;
    opacity: 0.6;
}
.legend-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 5px;
}
.diagnostic-table {
    width: 100%;
    font-size: 12px;
    border-collapse: collapse;
    margin: 8px 0;
}
.diagnostic-table th,
.diagnostic-table td {
    padding: 5px 2px;
    text-align: right;
    border-bottom: 1px solid #8883;
}
.diagnostic-table th:first-child,
.diagnostic-table td:first-child {
    text-align: left;
}
.job-error {
    overflow-wrap: anywhere;
}
.system-page :deep(.q-item__label--caption) {
    color: var(--q-text-muted);
}
@media (max-width: 1000px) {
    .overview-grid {
        grid-template-columns: 1fr;
    }
    .diagnostics {
        max-height: none;
    }
    .map-panel {
        height: 55vh;
        min-height: 400px;
    }
    .filters {
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }
}
@media (max-width: 600px) {
    .summary-grid,
    .filters {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .system-page {
        padding: 12px;
    }
}
</style>
