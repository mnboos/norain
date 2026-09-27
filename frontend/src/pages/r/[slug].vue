<route lang="json5">
{
    name: "public-route",
    meta: { title: "Öffentliche Route" },
}
</route>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { copyToClipboard, useQuasar } from "quasar";
import { format } from "date-fns";
import { de } from "date-fns/locale";
import {
    symSharpArrowBack,
    symSharpBookmarkAdd,
    symSharpFavorite,
    symSharpSchedule,
    symSharpShare,
    symSharpStraighten,
    symSharpTrendingUp,
} from "@quasar/extras/material-symbols-sharp";
import ElevationChart from "@/components/ElevationChart.vue";
import ForecastDetails from "@/components/ForecastDetails.vue";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import KeyRideDataCard from "@/components/KeyRideDataCard.vue";
import NiceMap from "@/components/NiceMap.vue";
import PhotoGallery from "@/components/sharing/PhotoGallery.vue";
import RouteComments from "@/components/sharing/RouteComments.vue";
import { useRoutePosition } from "@/composables/useRoutePosition";
import { useSession } from "@/composables/useSession";
import {
    publicRouteLink,
    useCopyRoute,
    useLikeMutation,
    usePublicRoute,
    usePublicRouteForecast,
} from "@/queries/publicRoutes";
import { apiErrorMessage } from "@/services/http";
import type { MapPoi } from "@/utils/poiCategories";

const PROFILE_LABELS: Record<string, string> = { bike: "Velo", ebike: "E-Bike", fast_ebike: "S-Pedelec" };

const currentRoute = useRoute();
const router = useRouter();
const $q = useQuasar();
const { isAuthenticated } = useSession();
const slug = computed(() => String(currentRoute.params.slug));

const { data: route, isLoading, error } = usePublicRoute(slug);
const like = useLikeMutation(slug);
const copy = useCopyRoute(slug);

watch(route, value => {
    if (value) document.title = value.name;
});

// -- the visitor's own ride --------------------------------------------------------

/** The next full hour today, or tomorrow at 08:00 once the evening has begun. */
function defaultDeparture(now = new Date()): Date {
    const next = new Date(now);
    next.setMinutes(0, 0, 0);
    next.setHours(next.getHours() + 1);
    if (next.getHours() >= 19 || next.getHours() < 6) {
        next.setDate(next.getDate() + (next.getHours() < 6 ? 0 : 1));
        next.setHours(8);
    }
    return next;
}
const initial = defaultDeparture();
const date = ref(format(initial, "yyyy-MM-dd"));
const time = ref(format(initial, "HH:mm"));
const wantsForecast = ref(false);
const forecastQuery = usePublicRouteForecast(slug, date, time, wantsForecast);
const forecast = computed(() => forecastQuery.data.value);
const { position, selectedSample, selectSample, selectPosition } = useRoutePosition(() => forecast.value);
const forecastError = ref("");
watch(forecastQuery.error, async value => {
    forecastError.value = value ? await apiErrorMessage(value, "Das Wetter konnte nicht berechnet werden.") : "";
});
const progress = computed(() => {
    const value = forecastQuery.progress.value;
    return value?.cellsTotal ? Math.round((100 * value.cellsSettled) / value.cellsTotal) : undefined;
});

// -- map -----------------------------------------------------------------------

const photoPois = computed<MapPoi[]>(() =>
    (route.value?.photos ?? [])
        .filter(p => p.lat != null && p.lon != null)
        .map(p => ({
            osmRef: p.id,
            lat: p.lat ?? 0,
            lon: p.lon ?? 0,
            category: "photo",
            name: p.caption,
            planned: true,
        })),
);

// -- actions ---------------------------------------------------------------------

function requireSignIn(): boolean {
    if (isAuthenticated.value) return true;
    void router.push({ path: "/account", query: { next: currentRoute.fullPath } });
    return false;
}

/** Planning a ride spends forecast budget, so it needs an account; reading the page does not. */
function planWeather() {
    if (requireSignIn()) wantsForecast.value = true;
}

function toggleLike() {
    if (!route.value || !requireSignIn()) return;
    like.mutate(!route.value.liked);
}

async function share() {
    const url = publicRouteLink(slug.value);
    // Missing on most desktop browsers, whatever the DOM types say.
    if ("share" in navigator) {
        await navigator.share({ title: route.value?.name, url }).catch(() => undefined);
        return;
    }
    await copyToClipboard(url);
    $q.notify({ type: "positive", message: "Link kopiert." });
}

async function saveCopy() {
    if (!requireSignIn()) return;
    try {
        const saved = await copy.mutateAsync();
        $q.notify({ type: "positive", message: "In deinen Routen gespeichert. Passe den Fahrplan dort an." });
        await router.push(`/routes/${saved.id}`);
    } catch (e) {
        $q.notify({
            type: "negative",
            message: await apiErrorMessage(e, "Die Route konnte nicht gespeichert werden."),
        });
    }
}

const km = (m: number) => `${(m / 1000).toFixed(1)} km`;
const duration = (s: number) => {
    const minutes = Math.round(s / 60);
    return minutes >= 60
        ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`
        : `${minutes} min`;
};
</script>

<template>
    <q-page class="q-pa-md">
        <q-btn flat no-caps :icon="symSharpArrowBack" label="Entdecken" to="/explore" class="q-mb-sm" />

        <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>
        <q-banner v-else-if="error || !route" class="bg-tint-warn" rounded>
            Diese Route gibt es nicht, oder sie ist nicht mehr öffentlich.
        </q-banner>

        <div v-else class="row q-col-gutter-md">
            <div class="col-12">
                <div class="row items-start q-gutter-sm">
                    <div class="col">
                        <h1 class="text-h5 text-weight-bold q-my-none">{{ route.name }}</h1>
                        <div class="text-caption text-muted">
                            von {{ route.author }}
                            <template v-if="route.publishedAt">
                                · {{ format(route.publishedAt, "d. MMMM yyyy", { locale: de }) }}
                            </template>
                        </div>
                    </div>
                    <q-btn
                        outline
                        no-caps
                        :color="route.liked ? 'negative' : undefined"
                        :icon="symSharpFavorite"
                        :label="String(route.likeCount ?? 0)"
                        :aria-label="route.liked ? 'Gefällt mir nicht mehr' : 'Gefällt mir'"
                        :aria-pressed="!!route.liked"
                        :loading="like.isPending.value"
                        @click="toggleLike"
                    />
                    <q-btn outline no-caps :icon="symSharpShare" label="Teilen" @click="share" />
                    <q-btn
                        v-if="!route.isOwner"
                        unelevated
                        no-caps
                        color="primary"
                        :icon="symSharpBookmarkAdd"
                        label="In meine Routen"
                        :loading="copy.isPending.value"
                        @click="saveCopy"
                    />
                </div>
                <div class="row q-gutter-xs q-mt-sm">
                    <q-chip dense :icon="symSharpStraighten" :label="km(route.distanceM)" />
                    <q-chip dense :icon="symSharpSchedule" :label="duration(route.durationS)" />
                    <q-chip
                        v-if="route.ascentM != null"
                        dense
                        :icon="symSharpTrendingUp"
                        :label="`${route.ascentM} m`"
                    />
                    <q-chip dense outline :label="PROFILE_LABELS[route.profile] ?? route.profile" />
                </div>
                <p v-if="route.description" class="q-mt-sm q-mb-none description">{{ route.description }}</p>
            </div>

            <div class="col-12 col-md-7">
                <NiceMap
                    :route-weather="forecast"
                    :preview-line="forecast ? undefined : route.line"
                    :position="position"
                    :pois="photoPois"
                    height="55vh"
                    @select-position="selectPosition"
                />
                <div v-if="route.photos.length" class="q-mt-md">
                    <h2 class="text-subtitle1 text-weight-bold q-my-sm">Fotos</h2>
                    <PhotoGallery :photos="route.photos" />
                </div>
                <ElevationChart
                    :public-slug="slug"
                    :position="position"
                    class="q-mt-md"
                    @select-position="selectPosition"
                />
            </div>

            <div class="col-12 col-md-5">
                <q-card flat bordered>
                    <q-card-section>
                        <h2 class="text-subtitle1 text-weight-bold q-my-none">Wetter für deine Fahrt</h2>
                        <p class="text-caption text-muted q-mb-sm">
                            Wann fährst du los? Das Wetter kommt für jeden Abschnitt zur Zeit, zu der du dort bist, bis
                            16 Tage im Voraus.
                        </p>
                        <div class="row q-col-gutter-sm items-end">
                            <q-input v-model="date" type="date" dense outlined label="Datum" class="col-6" />
                            <q-input v-model="time" type="time" dense outlined label="Abfahrt" class="col-4" />
                            <div class="col-2">
                                <q-btn
                                    unelevated
                                    no-caps
                                    color="primary"
                                    label="Los"
                                    :loading="forecastQuery.isFetching.value"
                                    @click="planWeather"
                                />
                            </div>
                        </div>
                        <q-linear-progress
                            v-if="forecastQuery.isFetching.value && progress !== undefined"
                            :value="progress / 100"
                            class="q-mt-sm"
                        />
                        <div v-if="forecastError" role="alert" class="text-negative q-mt-sm">{{ forecastError }}</div>
                    </q-card-section>
                    <template v-if="forecast">
                        <ForecastSummaryCard flat :forecast="forecast" />
                        <KeyRideDataCard flat :forecast="forecast" :columns="2" />
                        <ForecastDetails
                            :selected-sample="selectedSample"
                            :forecast="forecast"
                            @update:selected-sample="selectSample"
                        />
                    </template>
                </q-card>

                <q-card flat bordered class="q-mt-md">
                    <q-card-section>
                        <RouteComments :slug="slug" />
                    </q-card-section>
                </q-card>
            </div>
        </div>
    </q-page>
</template>

<style scoped>
.description {
    white-space: pre-wrap;
    max-width: 70ch;
}
</style>
