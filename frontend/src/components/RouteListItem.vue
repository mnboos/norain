<script setup lang="ts">
import { symSharpDelete, symSharpOpenInNew, symSharpRoute } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import { useI18n } from "vue-i18n";

import RouteThumbnail from "@/components/RouteThumbnail.vue";
import RouteWeatherBadges from "@/components/RouteWeatherBadges.vue";
import { liveThumbnail } from "@/utils/routeThumbnail";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { intlLocale } from "@/i18n";
import { rideLabelText } from "@/utils/levels";
import { useRecurringRoutes } from "@/queries/recurringRoutes";

const props = defineProps<{
    routeId: string;
}>();

const emit = defineEmits<{
    delete: [id: string];
    menuShown: [];
}>();

const { t } = useI18n();
const router = useRouter();

// The list query the page already runs: same key, so no request of its own per row.
const { data: routes } = useRecurringRoutes();
const route = computed(() => routes.value?.find(r => r.id === props.routeId));

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

function openInNewTab(id: string) {
    window.open(router.resolve(`/routes/${id}`).href, "_blank", "noopener");
}
</script>

<template>
    <q-item v-if="route" v-ripple :to="`/routes/${route.id}`" class="route-row q-py-sm q-pl-sm q-pr-none">
        <q-item-section avatar class="">
            <RouteThumbnail :route="route" />
        </q-item-section>
        <q-item-section>
            <q-item-label>{{ route.name }}</q-item-label>
            <q-item-label caption>
                {{ relativeTime(route.nextDeparture) }} · {{ qualityLabel(route) }}
            </q-item-label>
            <!--            <q-item-label caption>-->
            <!--                {{ route.startName }} → {{ route.destName }} · {{ profileLabel(route.profile) }}-->
            <!--            </q-item-label>-->
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
        <q-menu context-menu touch-position @show="emit('menuShown')">
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
</template>

<style scoped>
/* A long press opens our menu; without this iOS also shows its own link preview. */
.route-row {
    -webkit-touch-callout: none;
}
</style>
