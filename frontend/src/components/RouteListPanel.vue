<script setup lang="ts">
import {
    symSharpAdd,
    symSharpCasino,
    symSharpClose,
    symSharpDelete,
    symSharpLuggage,
    symSharpOpenInNew,
    symSharpRoute,
} from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import { useI18n } from "vue-i18n";

import RouteThumbnail from "@/components/RouteThumbnail.vue";
import RouteWeatherBadges from "@/components/RouteWeatherBadges.vue";
import { liveThumbnail } from "@/utils/routeThumbnail";
import { computed, ref, toRefs } from "vue";
import { useQuasar } from "quasar";
import { useRouter } from "vue-router";
import { intlLocale, te } from "@/i18n";
import { rideLabelText } from "@/utils/levels";

const props = withDefaults(
    defineProps<{
        routes: RecurringRouteOut[];
        loading: boolean;
        /** Quota state, from useEntitlements. Server-enforced; this only shapes the UI. */
        atRouteLimit?: boolean;
        maxRoutes?: number | null;
    }>(),
    { atRouteLimit: false, maxRoutes: null },
);

const { routes, loading, atRouteLimit, maxRoutes } = toRefs(props);
const { t } = useI18n();

const emit = defineEmits<{
    add: [];
    delete: [id: string];
    upgrade: [];
}>();

function relativeTime(iso: string | null | undefined): string {
    if (!iso) return t("routeList.noDeparture");
    const dt = new Date(iso);
    const now = new Date();
    const diffMs = dt.getTime() - now.getTime();
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 0) return t("routeList.past");
    if (diffMin < 60) return t("routeList.inMinutes", { n: diffMin });
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return t("routeList.inHours", { n: diffH });
    const diffD = Math.round(diffH / 24);
    if (diffD === 1) return t("routeList.tomorrow");
    return dt.toLocaleDateString(intlLocale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * The ride-quality wording for a route. The glyph beside it is coloured, but at 40 px with no
 * legend, so this caption is the only place the list says the quality - and why - in words.
 * It must never be dropped to save a line, and it must stay non-empty wherever a forecast exists.
 */
function qualityLabel(route: RecurringRouteOut): string {
    if (!route.hasGeometry) return t("routeList.computing");
    // A thumbnail computed for a departure that has since passed describes the wrong ride.
    const thumbnail = liveThumbnail(route);
    // The server scores the worst sample; no label means nothing could be scored yet.
    return rideLabelText(thumbnail?.rideLabel, thumbnail?.rideCause) || t("thumbnail.noForecast");
}

function profileLabel(profile: string): string {
    const key = `profiles.${profile}`;
    return te(key) ? t(key) : profile;
}

const $q = useQuasar();
const router = useRouter();
/** Phones swipe a row left to delete it; the delete button is for wider screens only. */
const swipeToDelete = computed(() => $q.screen.lt.sm);

/**
 * Swiping and long-pressing are invisible, so phones get a one-line hint until the rider has
 * used either once or closed it. Kept per browser only; storage may be unavailable.
 */
const GESTURE_HINT_KEY = "norain.routeGestureHintSeen";
function initialHintSeen(): boolean {
    try {
        return localStorage.getItem(GESTURE_HINT_KEY) === "1";
    } catch {
        return false;
    }
}
const gestureHintSeen = ref(initialHintSeen());
const showGestureHint = computed(() => swipeToDelete.value && !gestureHintSeen.value);
function dismissGestureHint() {
    if (gestureHintSeen.value) return;
    gestureHintSeen.value = true;
    try {
        localStorage.setItem(GESTURE_HINT_KEY, "1");
    } catch {
        // Not remembered: the hint comes back on the next visit.
    }
}

function onSwipeDelete(id: string, reset: () => void) {
    // Slide the row back at once: the confirmation dialog decides, and a deleted row leaves the list.
    reset();
    dismissGestureHint();
    emit("delete", id);
}

function openInNewTab(id: string) {
    window.open(router.resolve(`/routes/${id}`).href, "_blank", "noopener");
}

const addButtonLabel = computed(() => (atRouteLimit.value ? t("quota.title") : t("routeList.add")));
</script>

<template>
    <q-card
        :square="$q.screen.lt['sm']"
        :flat="$q.screen.lt['sm']"
        class="q-ma-xs-none q-ma-sm-md col-sm-9 col-md-6 col column"
    >
        <q-card-section v-if="!loading && routes.length" class="no-padding">
            <q-item-label header class="no-padding">
                <q-btn
                    flat
                    dense
                    :label="t('routeList.add')"
                    no-caps
                    :icon="symSharpAdd"
                    :disable="atRouteLimit"
                    :title="addButtonLabel"
                    @click="emit('add')"
                >
                    <q-tooltip>{{ addButtonLabel }}</q-tooltip>
                </q-btn>
                <q-btn flat dense no-caps :label="t('pages.journeys')" :icon="symSharpLuggage" to="/journeys" />
                <q-btn flat dense no-caps :label="t('routeList.randomRide')" :icon="symSharpCasino" to="/random" />
            </q-item-label>
            <q-separator />
        </q-card-section>
        <q-card-section v-if="loading" class="text-center q-my-auto">
            <q-spinner-dots size="2rem" />
        </q-card-section>
        <q-card-section v-else-if="!routes.length" class="text-center q-my-auto">
            <q-icon :name="symSharpRoute" size="3rem" />
            <q-item-label class="q-my-md">{{ t("routeList.empty") }}</q-item-label>
            <q-btn color="primary" :label="t('routeList.create')" @click="emit('add')" />
            <q-btn flat no-caps color="primary" :label="t('routeList.orJourney')" to="/journeys" class="q-ml-sm" />
            <q-btn flat no-caps color="primary" :label="t('routeList.orRandom')" to="/random" class="q-ml-sm" />
        </q-card-section>
        <q-card-section v-else class="q-pa-none col">
            <q-list separator class="col column">
                <!-- At the tier limit: say so where the add button just went dead. -->
                <q-item v-if="atRouteLimit" class="bg-grey-2 text-caption">
                    <q-item-section>
                        {{ t("routeList.full", { n: maxRoutes }) }}
                    </q-item-section>
                    <q-item-section side>
                        <q-btn dense flat color="primary" :label="t('routeList.seePlan')" @click="emit('upgrade')" />
                    </q-item-section>
                </q-item>

                <q-item v-if="showGestureHint" dense class="text-caption text-grey-7">
                    <q-item-section>Nach links wischen zum Löschen, lange drücken für weitere Aktionen.</q-item-section>
                    <q-item-section side>
                        <q-btn
                            flat
                            round
                            dense
                            size="sm"
                            :icon="symSharpClose"
                            aria-label="Hinweis schliessen"
                            @click="dismissGestureHint"
                        />
                    </q-item-section>
                </q-item>

                <!-- Route items. On a phone a row is deleted by swiping it left, on a wider
                     screen by its delete button; a right-click or long press opens the actions
                     as a menu. Every path asks in index.vue before it deletes. -->
                <q-slide-item
                    v-for="route in routes"
                    :key="route.id"
                    right-color="negative"
                    @right="({ reset }) => onSwipeDelete(route.id, reset)"
                >
                                        <template v-if="swipeToDelete" #right>
                        <q-icon :name="symSharpDelete" />
                    </template>
                    <q-item v-ripple :to="`/routes/${route.id}`" class="route-row q-py-sm q-pl-sm q-pr-none">
                        <q-item-section avatar class="">
                            <RouteThumbnail :route="route" />
                        </q-item-section>
                        <q-item-section>
                            <q-item-label>{{ route.name }}</q-item-label>
                            <q-item-label caption>
                                {{ relativeTime(route.nextDeparture) }} · {{ qualityLabel(route) }}
                            </q-item-label>
                            <q-item-label caption>
                                {{ route.startName }} → {{ route.destName }} · {{ profileLabel(route.profile) }}
                            </q-item-label>
                            <q-item-label v-if="route.returnRouteId" caption>
                                {{ t("routeList.return", { schedule: route.returnScheduleDescription }) }} ·
                                {{ relativeTime(route.returnNextDeparture) }}
                            </q-item-label>
                            <!-- Rain and frost: the two readings that decide whether you ride. They add
                         to the wording above, never replace it, and the line is there only when
                         there is rain or frost to report - the component owns its own label. -->
                            <RouteWeatherBadges :route="route" />
                        </q-item-section>
                        <q-item-section side>
                            <q-btn
                                flat
                                round
                                dense
                                size="sm"
                                :icon="symSharpDelete"
                                color="negative"
                                :aria-label="t('routes.delete.title')"
                                @click.stop.prevent="emit('delete', route.id)"
                            />
                        </q-item-section>
                        <q-menu context-menu touch-position @show="dismissGestureHint">
                            <q-list dense style="min-width: 180px">
                                <q-item v-close-popup clickable :to="`/routes/${route.id}`">
                                    <q-item-section avatar><q-icon :name="symSharpRoute" /></q-item-section>
                                    <q-item-section>Öffnen</q-item-section>
                                </q-item>
                                <q-item v-close-popup clickable @click="openInNewTab(route.id)">
                                    <q-item-section avatar><q-icon :name="symSharpOpenInNew" /></q-item-section>
                                    <q-item-section>In neuem Tab öffnen</q-item-section>
                                </q-item>
                                <q-separator />
                                <q-item v-close-popup clickable class="text-negative" @click="emit('delete', route.id)">
                                    <q-item-section avatar>
                                        <q-icon :name="symSharpDelete" color="negative" />
                                    </q-item-section>
                                    <q-item-section>Löschen</q-item-section>
                                </q-item>
                            </q-list>
                        </q-menu>
                    </q-item>
                </q-slide-item>
            </q-list>
        </q-card-section>
    </q-card>
</template>

<style scoped>
/* A long press opens our menu; without this iOS also shows its own link preview. */
.route-row {
    -webkit-touch-callout: none;
}
</style>
