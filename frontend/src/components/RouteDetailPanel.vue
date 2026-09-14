<script setup lang="ts">
import { computed, toRefs, ref, watch } from "vue";
import { symSharpCloudOff, symSharpMap } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import ForecastDetails from "@/components/ForecastDetails.vue";
import WeatherSummaryCard from "@/components/WeatherSummaryCard.vue";
import WeatherCharts from "@/components/WeatherCharts.vue";
import NiceMap from "@/components/NiceMap.vue";
import { useRecurringRoute, useRecurringRouteForecast } from "@/queries/recurringRoutes";

const props = defineProps<{
    route: RecurringRouteOut;
    departureDate: string;
    departureTime: string;
}>();

const { route, departureDate, departureTime } = toRefs(props);

const routeId = computed(() => route.value.id);

const refetchInterval = computed(() => (!route.value.hasGeometry ? 3000 : false));

// Fetch the full route data (for geometry status polling)
const { data: routeDetail } = useRecurringRoute(routeId, refetchInterval);

const hasGeometry = computed(() => routeDetail.value?.hasGeometry ?? route.value.hasGeometry);
const departureEnabled = computed(() => !!(hasGeometry.value && !!departureDate.value && !!departureTime.value));
const {
    data: forecast,
    isFetching: forecastLoading,
    error: forecastError,
    progress: forecastProgress,
} = useRecurringRouteForecast(routeId, departureDate, departureTime, departureEnabled);

// The forecast is assembled from one grid cell per ~1 km² of route, fetched by background
// workers. Showing how many have landed turns an indefinite wait into a determinate one.
const forecastProgressPercent = computed(() => {
    const progress = forecastProgress.value;
    if (!progress?.cellsTotal) return undefined;
    return Math.round((100 * progress.cellsSettled) / progress.cellsTotal);
});

const selectedSample = ref(0);
watch(forecast, () => {
    selectedSample.value = 0;
});

function profileLabel(profile: string): string {
    const labels: Record<string, string> = {
        bike: "Velo",
        ebike: "E-Bike",
        fast_ebike: "S-Pedelec",
    };
    return labels[profile] ?? profile;
}
</script>

<template>
    <q-card flat bordered class="q-ma-sm overflow-hidden">
        <q-card-section class="row items-center q-gutter-sm">
            <slot name="back" />
            <div class="col">
                <div class="row items-center q-gutter-sm">
                    <q-badge color="primary" outline>{{ profileLabel(route.profile) }}</q-badge>
                    <h1 class="text-subtitle1 text-weight-medium">{{ route.name }}</h1>
                </div>
                <div class="text-caption text-muted">
                    {{ route.startName }} → {{ route.destName }} · {{ route.scheduleDescription }}
                    <!--                    <template v-if="forecast">-->
                    <!--                        · Daten: {{ forecast.summary.source }}-->
                    <!--                        <template v-if="forecast.summary.stationCorrected">-->
                    <!--                            · kurzfristig mit Messstationen abgeglichen-->
                    <!--                        </template>-->
                    <!--                    </template>-->
                </div>
                <div v-if="route.description" class="text-caption text-muted">{{ route.description }}</div>
            </div>
            <!--            <q-btn-->
            <!--                v-if="forecast"-->
            <!--                flat-->
            <!--                dense-->
            <!--                no-caps-->
            <!--                color="primary"-->
            <!--                :icon="symSharpMap"-->
            <!--                label="Auf Karte anzeigen"-->
            <!--                :class="$q.screen.lt.sm ? 'col-12' : ''"-->
            <!--                :to="`/map?route=${route.id}`"-->
            <!--            />-->
        </q-card-section>
        <q-separator />

        <!-- A. Geometry pending -->
        <q-banner v-if="!hasGeometry" class="bg-tint-warn q-mb-sm" rounded>
            <template #avatar>
                <q-spinner-dots size="1.5rem" color="accent" />
            </template>
            Route wird berechnet… Die Streckendaten werden im Hintergrund geladen.
        </q-banner>

        <!-- B. No forecast available -->
        <q-banner v-else-if="!route.forecastAvailable && !forecastLoading" class="bg-tint-neutral q-mb-sm" rounded>
            <template #avatar>
                <q-icon :name="symSharpCloudOff" class="text-muted" />
            </template>
            Noch keine Vorhersage möglich. Die Wettervorhersage ist erst näher am Abfahrtstermin verfügbar.
        </q-banner>

        <!-- C. Forecast loaded -->
        <template v-if="forecast">
            <WeatherSummaryCard :forecast="forecast" :show-source="false" />
            <q-separator />
            <q-card-section :class="$q.dark.isActive ? 'bg-dark' : 'bg-grey-1'">
                <ForecastDetails v-model:selected-sample="selectedSample" :forecast="forecast" show-chart-key />
                <WeatherCharts
                    :job-id="forecast.jobId"
                    :version="forecast.version"
                    @select-sample="selectedSample = $event"
                />
            </q-card-section>
            <q-separator />
            <q-card-section>
                <div class="text-caption text-uppercase text-muted q-mb-sm">Strecke</div>
                <q-card flat bordered class="overflow-hidden">
                    <NiceMap
                        :route-weather="forecast"
                        :selected-sample="selectedSample"
                        :height="$q.screen.lt.md ? '300px' : '360px'"
                        @select-sample="selectedSample = $event"
                    />
                </q-card>
            </q-card-section>
        </template>

        <!-- Loading -->
        <div v-else-if="forecastLoading && hasGeometry" class="text-center q-mt-xl">
            <q-circular-progress
                v-if="forecastProgressPercent !== undefined"
                show-value
                :value="forecastProgressPercent"
                size="3rem"
                :thickness="0.2"
                color="primary"
                track-color="grey-3"
            />
            <q-spinner-dots v-else size="3rem" />
            <p class="text-grey">Wetterdaten werden geladen…</p>
        </div>

        <!-- Error -->
        <q-banner v-else-if="forecastError" class="bg-tint-error q-mt-md" rounded>
            Fehler beim Laden der Wetterdaten. Bitte versuche es später erneut.
        </q-banner>
    </q-card>
</template>
