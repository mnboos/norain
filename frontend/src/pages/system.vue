<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useQueryClient } from "@tanstack/vue-query";
import { useNow } from "@vueuse/core";
import { ResponseError } from "@norain/api/runtime";
import {
    CoreApiSystemMapFeaturesLayerEnum as Layer,
    CoreApiSystemMapFeaturesKindEnum as Kind,
    CoreApiSystemMapFeaturesSourceEnum as Source,
    CoreApiSystemMapFeaturesProfileEnum as Profile,
} from "@norain/api/apis";
import type { SystemFeature } from "@norain/api/models";
import SystemMap from "@/components/SystemMap.vue";
import { useSession } from "@/composables/useSession";
import { useBackendHost } from "@/utils";
import { CACHE_COLORS, COVERAGE_COLORS, COVERAGE_LABELS, cacheFreshness } from "@/utils/systemOverview";
import {
    systemKey,
    useSystemSummary,
    useSystemLayer,
    useSystemCoverage,
    useSystemCellHistory,
    useSystemJobs,
} from "@/queries/system";

definePage({ meta: { requiresAuth: true, requiresSystem: true } });
const { session, refreshSession } = useSession();
const allowed = computed(() => session.value.system?.allowed === true);
const queryClient = useQueryClient();
const viewport = ref<{ bbox: string; zoom: number } | null>(null);
const showRoutes = ref(true),
    showJourneys = ref(true),
    showCells = ref(true),
    alternatives = ref(false);
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
const allFeatures = computed(() => [...routes.items.value, ...journeys.items.value, ...cells.items.value]);
const queries = [summary, routes.query, journeys.query, cells.query, coverage, history, jobs];
const failed = computed(() => queries.some(query => query.isError.value));
const refreshing = computed(() => queries.some(query => query.isFetching.value));
const mapLoading = computed(
    () => routes.query.isFetching.value || journeys.query.isFetching.value || cells.query.isFetching.value,
);
const loaded = computed(() => allFeatures.value.length);
const total = computed(() => routes.total.value + journeys.total.value + cells.total.value);
const maxAge = computed(() => summary.data.value?.maxCellAgeSeconds ?? 7200);
const coverageCounts = computed(() =>
    Object.entries(COVERAGE_LABELS).map(([status, label]) => ({
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
              ["Wiederkehrende Routen", summary.data.value.recurringRoutes],
              ["Reisen", summary.data.value.journeys],
              ["Etappen inkl. Alternativen", summary.data.value.stages],
              ["Ohne Geometrie", summary.data.value.missingGeometry],
              ["Gespeicherte Zellstandorte", summary.data.value.cacheLocations],
          ]
        : [],
);
const profileOptions = [
    { label: "Alle Profile", value: null },
    ...Object.values(Profile).map(value => ({ label: value, value })),
];
const sourceOptions = [
    { label: "Beide Anbieter", value: Source.All },
    { label: "Open-Meteo", value: Source.OpenMeteo },
    { label: "OpenWeatherMap", value: Source.Openweathermap },
];
const activeOptions = [
    { label: "Alle Routen", value: null },
    { label: "Aktive Routen", value: true },
    { label: "Inaktive Routen", value: false },
];
const kindOptions = [
    { label: "Vorhersagen", value: Kind.Forecast },
    { label: "Ensemble", value: Kind.Ensemble },
];

function dateTime(value?: Date | null) {
    return value
        ? value.toLocaleString("de-CH", { timeZone: "Europe/Zurich", dateStyle: "short", timeStyle: "short" })
        : "–";
}
function age(value?: Date | null) {
    if (!value) return "–";
    const minutes = Math.max(0, Math.floor((now.value.getTime() - value.getTime()) / 60000));
    return minutes < 60 ? `${minutes} Min.` : `${Math.floor(minutes / 60)} Std. ${minutes % 60} Min.`;
}
function choose(feature: SystemFeature) {
    selected.value = feature;
    historyOffset.value = 0;
}
watch(allFeatures, items => {
    const current = selected.value;
    if (!current) return;
    const updated = items.find(
        item =>
            item.kind === current.kind &&
            (isCell.value ? item.lat === current.lat && item.lon === current.lon : item.id === current.id),
    );
    if (updated) selected.value = updated;
});
async function refresh() {
    mapError.value = "";
    await refreshSession();
    if (allowed.value) await queryClient.invalidateQueries({ queryKey: systemKey });
}
watch(failed, () => {
    if (
        queries.some(
            query =>
                query.error.value instanceof ResponseError && [401, 403].includes(query.error.value.response.status),
        )
    )
        void refreshSession();
});
watch([showRoutes, showJourneys, showCells, kind, source, day, active, profile, alternatives], () => {
    selected.value = null;
});
onBeforeUnmount(() => {
    void queryClient.cancelQueries({ queryKey: systemKey });
    queryClient.removeQueries({ queryKey: systemKey });
});
</script>

<template>
    <q-page class="system-page q-pa-md">
        <div class="row items-center q-gutter-sm q-mb-md">
            <div class="col">
                <h1 class="text-h5 q-my-none">Systemübersicht</h1>
                <div class="text-caption">Gespeicherte Daten · alle Benutzer · nur lesend</div>
            </div>
            <q-btn outline no-caps label="Aktualisieren" :loading="refreshing" @click="refresh" />
        </div>
        <q-banner v-if="!allowed" rounded class="bg-amber-2 text-dark">
            Für diese Übersicht ist eine verifizierte Administrator-Anmeldung erforderlich.
            <template #action>
                <q-btn
                    v-if="session.system"
                    flat
                    no-caps
                    label="Administrator-Anmeldung"
                    :href="`${useBackendHost()}${session.system.loginUrl}`"
                    target="_blank"
                    rel="noopener"
                />
            </template>
            <div class="text-caption">Nach der Anmeldung hier auf «Aktualisieren» klicken.</div>
        </q-banner>
        <template v-else>
            <q-banner v-if="failed || mapError" rounded class="bg-amber-2 text-dark q-mb-sm" role="alert">
                {{
                    failed
                        ? "Daten konnten nicht vollständig aktualisiert werden. Bereits geladene Daten bleiben sichtbar."
                        : mapError
                }}
                <template #action><q-btn flat no-caps label="Erneut versuchen" @click="refresh" /></template>
            </q-banner>
            <div class="text-caption q-mb-sm">
                Automatische Aktualisierung jede Minute bei sichtbarer Seite.
                <span v-if="summary.dataUpdatedAt.value">
                    Übersicht zuletzt geladen: {{ dateTime(new Date(summary.dataUpdatedAt.value)) }} (Zürich).
                </span>
            </div>
            <div class="summary-grid q-mb-md">
                <q-card v-for="[label, value] in summaryCards" :key="String(label)" flat bordered class="q-pa-sm">
                    <div class="text-h5">{{ value }}</div>
                    <div class="text-caption">{{ label }}</div>
                </q-card>
            </div>
            <q-linear-progress v-if="summary.isPending.value" indeterminate aria-label="Systemdaten werden geladen" />
            <div class="row q-gutter-sm items-center q-mb-sm">
                <q-checkbox v-model="showRoutes" label="Wiederkehrende Routen" dense />
                <q-checkbox v-model="showJourneys" label="Reisen" dense />
                <q-checkbox v-model="alternatives" label="Reise-Alternativen" :disable="!showJourneys" dense />
                <q-checkbox v-model="showCells" label="Wetterzellen" dense />
            </div>
            <div class="filters q-mb-sm">
                <q-select
                    v-model="profile"
                    :options="profileOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    label="Routingprofil"
                />
                <q-select
                    v-model="active"
                    :options="activeOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    label="Wiederkehrende Routen"
                    :disable="!showRoutes"
                />
                <q-select
                    v-model="kind"
                    :options="kindOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    label="Zelltyp / Abdeckung"
                />
                <q-select
                    v-model="source"
                    :options="sourceOptions"
                    emit-value
                    map-options
                    dense
                    outlined
                    label="Anbieter"
                    :disable="kind === Kind.Ensemble || !showCells"
                />
                <q-input
                    v-model="day"
                    type="date"
                    clearable
                    dense
                    outlined
                    label="Cache-Datum (optional)"
                    :disable="!showCells"
                />
            </div>
            <div class="text-caption q-mb-sm">
                Je Standort der neueste Eintrag nach Filterung. Das Cache-Datum ist der gespeicherte Tagesschlüssel,
                nicht die Abfahrtszeit.
            </div>
            <div class="row q-gutter-md text-caption q-mb-sm" aria-label="Kartenlegende">
                <span>
                    <i class="legend-line" :style="{ background: CACHE_COLORS.fresh }" />
                    Frisch (&lt; {{ maxAge / 7200 }} Std.)
                </span>
                <span>
                    <i class="legend-line" :style="{ background: CACHE_COLORS.aging }" />
                    Alternd (bis {{ maxAge / 3600 }} Std.)
                </span>
                <span>
                    <i class="legend-line" :style="{ background: CACHE_COLORS.stale }" />
                    Veraltet (&gt; {{ maxAge / 3600 }} Std.)
                </span>
                <span>
                    <i class="legend-line" style="background: #2186bd" />
                    Route
                </span>
                <span>
                    <i class="legend-line" style="background: #9264cf" />
                    Reise
                </span>
            </div>
            <div class="overview-grid">
                <div class="map-panel">
                    <div class="text-caption q-pa-xs">
                        Kartenausschnitt: {{ loaded }} / {{ total }} Objekte geladen.
                        <span v-if="mapLoading">Wird geladen …</span>
                        <span v-else-if="!total">Keine passenden Daten.</span>
                    </div>
                    <q-linear-progress v-if="mapLoading" indeterminate />
                    <SystemMap
                        v-if="summary.data.value"
                        :items="allFeatures"
                        :bounds="summary.data.value.bounds"
                        :selected="selected"
                        :points="coverage.data.value?.points ?? []"
                        :coverage-kind="kind"
                        :now="now.getTime()"
                        :max-age-seconds="maxAge"
                        @viewport="viewport = $event"
                        @select="choose"
                        @error="mapError = $event"
                    />
                    <div class="text-caption q-pa-xs">
                        NoRain-Cachegitter: 0.01° × 0.01°. Routen oder Zellen anklicken für Details.
                    </div>
                </div>
                <aside class="diagnostics" aria-label="Systemdiagnostik">
                    <q-card flat bordered class="q-mb-sm">
                        <q-card-section class="q-pa-sm">
                            <div class="row items-center">
                                <h2 class="text-subtitle1 col q-my-none">
                                    {{ selected ? (isCell ? "Wetterzelle" : selected.name) : "Auswahl" }}
                                </h2>
                                <q-btn v-if="selected" flat dense label="Schliessen" no-caps @click="selected = null" />
                            </div>
                            <p v-if="!selected" class="text-caption q-mb-none">
                                Eine Route zeigt die Cache-Abdeckung ihrer nächsten Abfahrt. Eine Zelle zeigt
                                gespeicherte Einträge aller Anbieter und Tage.
                            </p>
                            <template v-else-if="isCell">
                                <div class="text-caption">
                                    {{ selected.lat?.toFixed(2) }}, {{ selected.lon?.toFixed(2) }} ·
                                    {{ selected.source }}
                                </div>
                                <div class="text-caption">
                                    Geladen: {{ dateTime(selected.fetchedAt) }} · Alter: {{ age(selected.fetchedAt) }}
                                </div>
                                <div class="text-caption">
                                    Angeforderter Horizont: {{ selected.forecastDays }} Tage. Frische allein bedeutet
                                    keine Abdeckung einer Abfahrt.
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
                                                {{ dateTime(record.fetchedAt) }} · {{ record.forecastDays }} Tage
                                                <br />
                                                Cache: {{ record.dayKey?.toISOString().slice(0, 10) }}
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
                                        label="Zurück"
                                        :disable="!historyOffset"
                                        @click="historyOffset = Math.max(0, historyOffset - 25)"
                                    />
                                    <span class="text-caption">{{ history.data.value?.total ?? 0 }} Einträge</span>
                                    <q-btn
                                        flat
                                        dense
                                        no-caps
                                        label="Weiter"
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
                                        {{ Math.round((coverage.data.value.durationSeconds ?? 0) / 60) }} Min.
                                    </div>
                                    <div class="text-caption">
                                        Geometrie: {{ dateTime(coverage.data.value.geometryFetchedAt) }}
                                    </div>
                                    <div class="text-caption">
                                        Abfahrt: {{ dateTime(coverage.data.value.departure) }} (Zürich)
                                    </div>
                                    <p v-if="coverage.data.value.unavailable" class="q-mt-sm q-mb-none">
                                        {{ coverage.data.value.unavailable }}
                                    </p>
                                    <template v-else>
                                        <div class="text-caption q-mt-sm">
                                            Zellstandorte für die feste Abfahrt, ohne flexible Alternativzeiten.
                                        </div>
                                        <table class="diagnostic-table">
                                            <thead>
                                                <tr>
                                                    <th>Status</th>
                                                    <th>Prognose</th>
                                                    <th>Ensemble</th>
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
                                            Kartenpunkte zeigen
                                            {{ kind === Kind.Forecast ? "Prognose" : "Ensemble" }}-Abdeckung. Nicht
                                            ausreichend: Horizont, Daten oder Version passen nicht.
                                        </div>
                                    </template>
                                </template>
                            </template>
                        </q-card-section>
                    </q-card>
                    <q-expansion-item
                        label="Cache-Bestand (gesamtes System)"
                        default-opened
                        class="bordered-panel q-mb-sm"
                    >
                        <div class="q-pa-sm">
                            <div v-if="!summary.data.value?.caches.length" class="text-caption">
                                Noch keine Wetterzellen gespeichert.
                            </div>
                            <div
                                v-for="cache in summary.data.value?.caches"
                                :key="cache.kind + cache.source"
                                class="q-mb-sm"
                            >
                                <div class="text-body2">{{ cache.source }}</div>
                                <div class="text-caption">
                                    {{ cache.locations }} Standorte · {{ cache.records }} Einträge
                                </div>
                                <div class="text-caption">{{ cache.fresh }} frisch · {{ cache.stale }} veraltet</div>
                            </div>
                            <div class="text-caption">
                                Ein Standort kann mehrere Tage, Anbieter und Zelltypen enthalten.
                            </div>
                        </div>
                    </q-expansion-item>
                    <q-expansion-item label="Vorhersage-Aufträge" default-opened class="bordered-panel">
                        <div class="q-pa-sm text-caption">
                            Alle offenen Aufträge sowie abgeschlossene/fehlgeschlagene Aufträge der letzten 24 Stunden.
                        </div>
                        <q-linear-progress v-if="jobs.isFetching.value" indeterminate />
                        <div v-if="jobs.data.value && !jobs.data.value.items.length" class="q-pa-sm text-caption">
                            Keine Aufträge in diesem Zeitraum.
                        </div>
                        <q-list dense separator>
                            <q-item v-for="job in jobs.data.value?.items" :key="job.id">
                                <q-item-section>
                                    <q-item-label>{{ job.kind }} · {{ job.status }}</q-item-label>
                                    <q-item-label v-if="job.possiblyStalled" class="text-negative">
                                        Möglicherweise stehen geblieben
                                    </q-item-label>
                                    <q-item-label caption>
                                        {{ job.cellsSettled }} / {{ job.cellsTotal }} Zellen bearbeitet ·
                                        {{ job.cellsFailed }} fehlgeschlagen
                                    </q-item-label>
                                    <q-item-label caption>
                                        Erstellt: {{ dateTime(job.createdAt) }}
                                        <br />
                                        Aktualisiert: {{ dateTime(job.updatedAt) }}
                                    </q-item-label>
                                    <q-item-label v-if="job.error" caption class="job-error">
                                        {{ job.error }}
                                    </q-item-label>
                                </q-item-section>
                            </q-item>
                        </q-list>
                        <div class="row items-center justify-between q-pa-xs">
                            <q-btn
                                flat
                                dense
                                no-caps
                                label="Zurück"
                                :disable="!jobsOffset"
                                @click="jobsOffset = Math.max(0, jobsOffset - 25)"
                            />
                            <span class="text-caption">{{ jobs.data.value?.total ?? 0 }} Aufträge</span>
                            <q-btn
                                flat
                                dense
                                no-caps
                                label="Weiter"
                                :disable="jobs.data.value?.nextOffset == null"
                                @click="jobsOffset = jobs.data.value!.nextOffset!"
                            />
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
