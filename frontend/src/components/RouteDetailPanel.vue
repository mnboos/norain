<script setup lang="ts">
import { useRoutePosition } from "@/composables/useRoutePosition";
import ElevationChart from "@/components/ElevationChart.vue";
import { GeometrySource } from "@norain/api/models";
import { computed, ref, toRefs, watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { te, tp } from "@/i18n";
import {
    symSharpCloudOff,
    symSharpPedalBike,
    symSharpEditRoad,
    symSharpDownload,
    symSharpPublic,
    symSharpShare,
    symSharpMoreVert,
} from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import { useEntitlements } from "@/composables/useEntitlements";
import DepartureFlexibility from "@/components/DepartureFlexibility.vue";
import DepartureComparison from "@/components/DepartureComparison.vue";
import ForecastFreshness from "@/components/ForecastFreshness.vue";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import KeyRideDataCard from "@/components/KeyRideDataCard.vue";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
import WeatherChart from "@/components/WeatherChart.vue";
import { CHART_STYLE } from "@/utils/chartStyle";
import NiceMap from "@/components/NiceMap.vue";
import RouteEditorDialog from "@/components/RouteEditorDialog.vue";
import RouteTimingFields from "@/components/RouteTimingFields.vue";
import RouteShareDialog from "@/components/sharing/RouteShareDialog.vue";
import { canShareFiles, gpxApi, shareGpx, gpxError } from "@/services/gpx";
import { toLonLat, type LonLat } from "@/utils/routeEditing";
import { useRecurringRoute, useRecurringRouteForecast, useUpdateRecurringRoute } from "@/queries/recurringRoutes";

const props = defineProps<{
    route: RecurringRouteOut;
    departureDate: string;
    departureTime: string;
}>();

const { route, departureDate, departureTime } = toRefs(props);
const chartCardStyle = { minHeight: `${CHART_STYLE.height.compact}px` };
const $q = useQuasar();
const { t } = useI18n();
const { isPro } = useEntitlements();

const routeId = computed(() => route.value.id);
const hasGeometry = computed(() => !!route.value.hasGeometry);
const profileLabel = computed(() => {
    const key = `profiles.${route.value.profile}`;
    return te(key) ? t(key) : route.value.profile;
});

// Polls until the geometry is built. The page reads the same query key, so `route` updates with it.
useRecurringRoute(routeId, () => (hasGeometry.value ? false : 3000));

const sharingOpen = ref(false);
const flexBefore = ref(route.value.departureFlexBeforeMinutes ?? 0);
const flexAfter = ref(route.value.departureFlexAfterMinutes ?? 0);
const selectedDeparture = ref<string | null>(null);
watch(
    () => [route.value.id, route.value.departureFlexBeforeMinutes, route.value.departureFlexAfterMinutes],
    () => {
        flexBefore.value = route.value.departureFlexBeforeMinutes ?? 0;
        flexAfter.value = route.value.departureFlexAfterMinutes ?? 0;
    },
);
watch(
    [routeId, departureDate, departureTime, flexBefore, flexAfter],
    () => {
        selectedDeparture.value = null;
    },
    { flush: "sync" },
);
const comparisonQuery = useRecurringRouteForecast(
    routeId,
    departureDate,
    departureTime,
    () => hasGeometry.value && !!departureDate.value && !!departureTime.value,
    () => (isPro.value ? flexBefore.value : 0),
    () => (isPro.value ? flexAfter.value : 0),
);
const selectedQuery = useRecurringRouteForecast(
    routeId,
    () => selectedDeparture.value?.slice(0, 10) ?? departureDate.value,
    () => selectedDeparture.value?.slice(11) ?? departureTime.value,
    () => hasGeometry.value && selectedDeparture.value !== null,
    0,
    0,
);
const forecast = computed(() => (selectedDeparture.value ? selectedQuery.data.value : comparisonQuery.data.value));
const forecastLoading = computed(() =>
    selectedDeparture.value ? selectedQuery.isFetching.value : comparisonQuery.isFetching.value,
);
const forecastError = computed(() =>
    selectedDeparture.value ? selectedQuery.error.value : comparisonQuery.error.value,
);
const forecastProgress = computed(() =>
    selectedDeparture.value ? selectedQuery.progress.value : comparisonQuery.progress.value,
);
const departureComparison = computed(() => comparisonQuery.data.value?.departureComparison);
const saveWindow = useUpdateRecurringRoute();
const windowChanged = computed(
    () =>
        flexBefore.value !== (route.value.departureFlexBeforeMinutes ?? 0) ||
        flexAfter.value !== (route.value.departureFlexAfterMinutes ?? 0),
);
function saveFlexibility() {
    saveWindow.mutate({
        id: route.value.id,
        data: {
            ...route.value,
            departureFlexBeforeMinutes: flexBefore.value,
            departureFlexAfterMinutes: flexAfter.value,
        },
    });
}

// Reshaping is done on the outbound route; the server mirrors its via points onto the return.
const editing = ref(false);
const exporting = ref(false);
const sharing = canShareFiles();
const duration = ref(route.value.durationSeconds ?? 0);
watch(
    () => route.value.durationSeconds,
    value => {
        duration.value = value ?? 0;
    },
);
async function exportRoute() {
    exporting.value = true;
    try {
        const response = await gpxApi.coreApiGpxExportSavedGpxRaw({ routeId: route.value.id });
        await shareGpx(response.raw, route.value.name);
    } catch (e) {
        $q.notify({ type: "negative", message: await gpxError(e) });
    } finally {
        exporting.value = false;
    }
}

async function saveDuration() {
    await saveShape(
        { id: route.value.id, data: { ...route.value, durationSeconds: duration.value } },
        {
            onError: e => {
                void gpxError(e).then(message => {
                    $q.notify({ type: "negative", message });
                });
            },
        },
    );
}

const { mutateAsync: saveShape, isPending: isSavingShape } = useUpdateRecurringRoute();
const canEditShape = computed(
    () => !route.value.parentRouteId && route.value.geometrySource !== GeometrySource.Imported,
);
const viaPoints = computed(() => (route.value.viaPoints ?? []).map(toLonLat));
async function saveViaPoints(points: LonLat[]) {
    await saveShape(
        { id: route.value.id, data: { ...route.value, viaPoints: points } },
        { onError: () => $q.notify({ type: "negative", message: t("routeDetail.shapeSaveFailed") }) },
    );
}

// The forecast is assembled from one grid cell per ~1 km² of route, fetched by background
// workers. Showing how many have landed turns an indefinite wait into a determinate one.
const forecastProgressPercent = computed(() => {
    const progress = forecastProgress.value;
    if (!progress?.cellsTotal) return undefined;
    return Math.round((100 * progress.cellsSettled) / progress.cellsTotal);
});

// Renewing on the server, not just any refetch: the 60 s one answered with a finished job
// keeps the progress at "done" and must not flash the line in and out.
const refreshing = computed(() => {
    const status = forecastProgress.value?.status;
    return forecastLoading.value && !!status && status !== "done" && status !== "failed";
});
const refreshFailed = computed(() => !!forecastError.value && !forecastLoading.value);
const { position, positionMinutes, selectPosition, selectMinutes } = useRoutePosition(() => forecast.value);
</script>

<template>
    <!-- Two parts share the height: the cards take what they need, the map fills the rest. -->
    <q-card class="column transparent" flat square>
        <q-card-section class="row q-pa-none q-pa-sm">
            <!--            Routendetails (Name, etc) -->
            <div class="col-xs-12 col-sm-4 col-md-6 column">
                <q-card class="q-mr-sm-sm q-mr-none-xs q-mb-xs-sm q-mb-sm-none col">
                    <q-card-section class="no-padding">
                        <q-item class="">
                            <!--                        <q-item-section side>-->
                            <!--                            <slot name="back" />-->
                            <!--                        </q-item-section>-->
                            <!-- Left out where this card is at its narrowest, so the name keeps its room. -->
                            <!--                        <q-item-section v-if="$q.screen.width >= 1280 || $q.screen.lt.md" avatar>-->
                            <!--                            <q-avatar rounded class="bg-tint-wet" text-color="primary" :icon="symSharpMap" />-->
                            <!--                        </q-item-section>-->
                            <q-item-section>
                                <q-item-label class="text-h6">
                                    {{ route.name }}
                                </q-item-label>
                            </q-item-section>
                            <q-item-section side class="no-padding">
                                <q-btn :icon="symSharpMoreVert" dense flat round>
                                    <q-menu auto-close cover anchor="top middle" class="row">
                                        <q-list>
                                            <q-item
                                                clickable
                                                flat
                                                dense
                                                no-caps
                                                class="col-12"
                                                :icon="symSharpEditRoad"
                                                color="primary"
                                                :label="t('routeEditor.title')"
                                                :loading="isSavingShape"
                                                @click="editing = true"
                                            >
                                                <q-item-section side>
                                                    <q-icon :name="symSharpEditRoad" />
                                                </q-item-section>

                                                {{ t("routeEditor.title") }}
                                            </q-item>
                                            <q-item
                                                clickable
                                                flat
                                                class="col-12"
                                                dense
                                                no-caps
                                                :disable="!hasGeometry && route.geometrySource !== 'imported'"
                                                :loading="exporting"
                                                @click="exportRoute"
                                            >
                                                <q-item-section side>
                                                    <q-icon :name="sharing ? symSharpShare : symSharpDownload" />
                                                </q-item-section>
                                                <q-item-section>
                                                    {{
                                                        sharing
                                                            ? t("routeDetail.shareGpx")
                                                            : t("routeDetail.downloadGpx")
                                                    }}
                                                </q-item-section>
                                            </q-item>
                                            <q-btn
                                                flat
                                                class="col-12"
                                                dense
                                                no-caps
                                                :icon="route.visibility === 'public' ? symSharpPublic : symSharpShare"
                                                :label="
                                                    route.visibility === 'public'
                                                        ? t('routeDetail.publicPhotos')
                                                        : t('routeDetail.sharePhotos')
                                                "
                                                :color="route.visibility === 'public' ? 'primary' : undefined"
                                                @click="sharingOpen = true"
                                            />
                                        </q-list>
                                        <RouteShareDialog
                                            v-if="sharingOpen"
                                            v-model="sharingOpen"
                                            :route-id="route.id"
                                            :route-name="route.name"
                                        />
                                    </q-menu>
                                </q-btn>
                            </q-item-section>
                        </q-item>
                    </q-card-section>
                    <q-card-section class="q-pt-none text-caption text-muted">
                        <q-item-label>{{ route.startName }} → {{ route.destName }}</q-item-label>
                        <q-item-label caption>{{ route.scheduleDescription }}</q-item-label>
                        <q-item-label v-if="route.description">{{ route.description }}</q-item-label>
                        <q-chip
                            dense
                            outline
                            color="primary"
                            :icon="symSharpPedalBike"
                            :label="profileLabel"
                            class="q-mx-none q-mb-none q-mt-md"
                        />
                    </q-card-section>
                    <q-card-section
                        class="q-py-none"
                        v-if="route.geometrySource === 'imported' && !route.parentRouteId"
                    >
                        <div class="q-my-sm">{{ t("routeForm.originalFromGpx") }}</div>
                        <RouteTimingFields
                            v-model="duration"
                            :profile="route.profile"
                            :distance-m="route.totalDistanceM ?? 0"
                        />
                        <q-btn
                            flat
                            no-caps
                            :label="tp(route.profile, 'routeDetail.saveDuration')"
                            :disable="duration <= 0 || duration > 1382400 || duration === route.durationSeconds"
                            :loading="isSavingShape"
                            @click="saveDuration"
                        />
                    </q-card-section>
                    <q-separator inset />
                    <DepartureFlexibility
                        v-model:before="flexBefore"
                        v-model:after="flexAfter"
                        :profile="route.profile"
                    >
                        <q-btn
                            v-if="windowChanged"
                            flat
                            no-caps
                            :label="t('routeDetail.saveForRoute')"
                            :loading="saveWindow.isPending.value"
                            @click="saveFlexibility"
                        />
                        <div v-if="saveWindow.isError.value" role="alert">
                            {{ t("routeDetail.windowSaveFailed") }}
                        </div>
                    </DepartureFlexibility>
                    <DepartureComparison
                        v-if="departureComparison"
                        better-only
                        :profile="route.profile"
                        :comparison="departureComparison"
                        :selected-time="selectedDeparture"
                        @select="selectedDeparture = $event"
                        @reset="selectedDeparture = null"
                    />
                </q-card>
            </div>

            <!--            Übersichtskarte-->
            <ForecastSummaryCard class="col-xs-12 col-sm-8 col-md-6 column" v-if="forecast" :forecast="forecast">
                <template #after>
                    <q-card-section
                        v-if="
                            !hasGeometry ||
                            (forecastError && !forecast) ||
                            (!route.forecastAvailable && !forecastLoading)
                        "
                        class="col-xs-12 col-sm-6 col-md-6"
                    >
                        <q-banner v-if="!hasGeometry" rounded class="bg-tint-warn">
                            <template #avatar>
                                <q-spinner-dots size="1.5rem" color="accent" />
                            </template>
                            {{ t("routeList.computing") }}
                        </q-banner>
                        <q-banner v-else-if="forecastError && !forecast" rounded class="bg-tint-error">
                            {{ t("routeDetail.weatherFailedPaused") }}
                            <q-btn flat to="/account" :label="t('routeDetail.manageRoutes')" no-caps />
                        </q-banner>
                        <q-banner v-else rounded class="bg-tint-neutral">
                            <template #avatar>
                                <q-icon :name="symSharpCloudOff" class="text-muted" />
                            </template>
                            {{ tp(route.profile, "routeDetail.tooEarly") }}
                        </q-banner>
                    </q-card-section>
                    <template v-if="forecast">
                        <q-separator />
                        <KeyRideDataCard flat :forecast="forecast" />
                    </template>
                    <q-skeleton v-else />
                </template>
            </ForecastSummaryCard>
            <q-skeleton v-else />

            <!--                Wind-->
            <!--            <q-card class="col-12 col-sm-4 col-md-4 column">-->
            <!--                <q-card-section class="q-pa-none col">-->
            <!--                    <WeatherChart-->
            <!--                        v-if="forecast"-->
            <!--                        kind="headwind"-->
            <!--                        :profile="forecast.profile"-->
            <!--                        :version="forecast.version"-->
            <!--                        :cursor-minutes="positionMinutes"-->
            <!--                        :samples="forecast.samples"-->
            <!--                        @select-minutes="selectMinutes"-->
            <!--                        :style="{ minHeight: '180px' }"-->
            <!--                    />-->
            <!--                </q-card-section>-->
            <!--            </q-card>-->
            <q-card-section class="col-12 col-sm-4 col-md-4 column q-px-none">
                <WeatherChart
                    v-if="forecast"
                    kind="headwind"
                    :profile="forecast.profile"
                    :version="forecast.version"
                    :cursor-minutes="positionMinutes"
                    :samples="forecast.samples"
                    @select-minutes="selectMinutes"
                    :style="chartCardStyle"
                />
                <q-skeleton v-else />
            </q-card-section>

            <!--            Temperatur -->
            <q-card-section class="col-12 col-sm-4 col-md-4 column q-px-sm-sm q-px-xs-none">
                <WeatherChart
                    v-if="forecast"
                    kind="temperature"
                    :profile="forecast.profile"
                    :version="forecast.version"
                    :cursor-minutes="positionMinutes"
                    :samples="forecast.samples"
                    @select-minutes="selectMinutes"
                    :style="chartCardStyle"
                />
                <q-skeleton v-else />
            </q-card-section>

            <q-card-section class="col-12 col-sm-4 col-md-4 column q-px-none">
                <ElevationChart
                    :route-id="route.id"
                    compact
                    class="col fit column"
                    :profile="route.profile"
                    :version="String(route.updatedAt)"
                    :position="position"
                    @select-position="selectPosition"
                    :style="chartCardStyle"
                />
            </q-card-section>

            <q-card-section
                v-if="!hasGeometry || (forecastError && !forecast) || (!route.forecastAvailable && !forecastLoading)"
                class="col-xs-12 col-sm-6 col-md-6"
            >
                <q-banner v-if="!hasGeometry" rounded class="bg-tint-warn">
                    <template #avatar>
                        <q-spinner-dots size="1.5rem" color="accent" />
                    </template>
                    {{ t("routeList.computing") }}
                </q-banner>
                <q-banner v-else-if="forecastError && !forecast" rounded class="bg-tint-error">
                    {{ t("routeDetail.weatherFailedPaused") }}
                    <q-btn flat to="/account" :label="t('routeDetail.manageRoutes')" no-caps />
                </q-banner>
                <q-banner v-else rounded class="bg-tint-neutral">
                    <template #avatar>
                        <q-icon :name="symSharpCloudOff" class="text-muted" />
                    </template>
                    {{ tp(route.profile, "routeDetail.tooEarly") }}
                </q-banner>
            </q-card-section>
        </q-card-section>

        <!-- On phones the cards stack and the map keeps a fixed height; on a wide screen it
             fills what the cards leave, but never less than 300 px. -->
        <q-card-section class="q-pa-none column col">
            <NiceMap :route-weather="forecast" :position="position" @select-position="selectPosition" class="col" />
        </q-card-section>
        <!--            -->

        <!-- A forecast on screen (a stale one while it refreshes) stays usable: the line above says so. -->
        <q-inner-loading :showing="forecastLoading && hasGeometry && !forecast">
            <q-circular-progress
                v-if="forecastProgressPercent !== undefined"
                show-value
                :value="forecastProgressPercent"
                size="3rem"
                :thickness="0.2"
                color="primary"
                track-color="grey-3"
            />
            <q-spinner-dots v-else size="3rem" color="primary" />
            <div class="text-muted q-mt-sm">{{ t("forecast.loading") }}</div>
        </q-inner-loading>

        <RouteEditorDialog
            v-model="editing"
            :start="[route.startLon, route.startLat]"
            :dest="[route.destLon, route.destLat]"
            :profile="route.profile"
            :via-points="viaPoints"
            @apply="saveViaPoints"
        />
    </q-card>
</template>
