<route lang="json5">
{
    name: "route-detail",
    meta: { titleKey: "pages.route", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useRouteQuery } from "@vueuse/router";
import { useI18n } from "vue-i18n";
import { tp } from "@/i18n";
import { symSharpArrowBack } from "@quasar/extras/material-symbols-sharp";
import RouteDetailPanel from "@/components/RouteDetailPanel.vue";
import { useSession } from "@/composables/useSession";
import { nextDepartureParts, useRecurringRoute } from "@/queries/recurringRoutes";
import { recordRouteOpened } from "@/utils/recentRoutes";
import { nextRideId } from "@/utils/nextRide";

type Direction = "outbound" | "return";

const { t } = useI18n();
const currentRoute = useRoute();
const router = useRouter();
const routeId = computed(() => String(currentRoute.params.id));
const { session } = useSession();

// The tab lives in the query (?direction=outbound|return), so a reload or a shared link
// keeps it. Without one, the tab follows the clock (see `timedDirection`).
const directionQuery = useRouteQuery<string | undefined>("direction", undefined, { mode: "replace" });

const { data: route, isLoading, error } = useRecurringRoute(routeId);

// The page belongs to the outbound route; a link to a return route opens it on the return tab.
watch(
    () => route.value?.parentRouteId,
    parentId => {
        if (parentId)
            void router.replace({ path: `/routes/${parentId}`, query: { ...currentRoute.query, direction: "return" } });
    },
    { immediate: true },
);
const outbound = computed(() => (route.value && !route.value.parentRouteId ? route.value : undefined));
const hasReturn = computed(() => !!outbound.value?.returnRouteId);

// The ride under way, else the next one: the server keeps a ride's departure as
// `nextDeparture` until it has been ridden, so this stays put while the rider is on the road.
const timedDirection = computed<Direction>(() =>
    outbound.value && hasReturn.value && nextRideId(outbound.value) === outbound.value.returnRouteId
        ? "return"
        : "outbound",
);
const direction = computed<Direction>({
    get: () => {
        if (directionQuery.value === "return") return hasReturn.value ? "return" : "outbound";
        if (directionQuery.value === "outbound") return "outbound";
        return timedDirection.value;
    },
    set: value => {
        directionQuery.value = value;
    },
});

const returnId = computed(() => (direction.value === "return" ? outbound.value?.returnRouteId : null));
const { data: returnRoute, isLoading: returnLoading, error: returnError } = useRecurringRoute(returnId);
const shown = computed(() => (direction.value === "return" ? returnRoute.value : outbound.value));

// Remembered so the dashboard can load this route's forecast ahead next time.
watch(
    () => outbound.value?.id,
    id => {
        if (!id) return;
        const user = session.value.user;
        recordRouteOpened(user?.id ?? user?.username ?? "", id);
    },
    { immediate: true },
);

// The same departure the dashboard prefetches, so the forecast is found in the cache.
const departure = computed(() => (shown.value ? nextDepartureParts(shown.value) : { date: "", time: "" }));
const departureDate = computed(() => departure.value.date);
const departureTime = computed(() => departure.value.time);
</script>

<template>
    <q-page class="column fit">
        <!-- Once the route is loaded, the back button moves into the panel's one-line header. -->
        <q-btn v-if="!shown" flat :icon="symSharpArrowBack" :label="t('common.back')" to="/routes" class="self-start" />

        <div
            v-if="isLoading || route?.parentRouteId || (direction === 'return' && returnLoading)"
            class="text-center q-mt-xl"
        >
            <q-spinner-dots size="3rem" />
        </div>

        <q-banner v-else-if="error || returnError || !shown" class="bg-tint-error q-mt-md" rounded>
            {{ t("routes.notFound") }}
        </q-banner>

        <q-tabs
            v-if="outbound && hasReturn"
            v-model="direction"
            dense
            align="left"
            class="q-ma-none no-padding"
            no-caps
        >
            <q-tab name="outbound" :label="tp(outbound.profile, 'routes.outbound')" />
            <q-tab name="return" :label="tp(outbound.profile, 'routes.return')" />
        </q-tabs>
        <RouteDetailPanel
            v-if="shown"
            :key="shown.id"
            :route="shown"
            :departure-date="departureDate"
            :departure-time="departureTime"
            class="col full-width"
        >
            <template #back>
                <q-btn flat round dense :icon="symSharpArrowBack" to="/routes" :aria-label="t('common.back')" />
            </template>
        </RouteDetailPanel>
    </q-page>
</template>
