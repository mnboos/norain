<script setup lang="ts">
import {
    symSharpAdd,
    symSharpCasino,
    symSharpClose,
    symSharpDelete,
    symSharpLuggage,
    symSharpRoute,
} from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import { useI18n } from "vue-i18n";

import RouteListItem from "@/components/RouteListItem.vue";
import { computed, ref, toRefs } from "vue";
import { useQuasar } from "quasar";

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

const $q = useQuasar();
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
                    <RouteListItem
                        :route-id="route.id"
                        @delete="emit('delete', $event)"
                        @menu-shown="dismissGestureHint"
                    />
                </q-slide-item>
            </q-list>
        </q-card-section>
    </q-card>
</template>

