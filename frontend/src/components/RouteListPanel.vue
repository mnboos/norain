<script setup lang="ts">
import { symSharpAdd, symSharpDelete, symSharpRoute } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";

import RouteThumbnail from "@/components/RouteThumbnail.vue";
import RouteWeatherBadges from "@/components/RouteWeatherBadges.vue";
import { liveThumbnail } from "@/utils/routeThumbnail";
import { computed, toRefs } from "vue";

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

const emit = defineEmits<{
    add: [];
    delete: [id: string];
    upgrade: [];
}>();

function relativeTime(iso: string | null | undefined): string {
    if (!iso) return "Keine Abfahrt";
    const dt = new Date(iso);
    const now = new Date();
    const diffMs = dt.getTime() - now.getTime();
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 0) return "Vergangen";
    if (diffMin < 60) return `in ${diffMin} min`;
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return `in ${diffH}h`;
    const diffD = Math.round(diffH / 24);
    if (diffD === 1) return "morgen";
    return dt.toLocaleDateString("de-CH", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * The ride-quality wording for a route. The glyph beside it is coloured, but at 40 px with no
 * legend, so this caption is the only place the list says the quality - and why - in words.
 * It must never be dropped to save a line, and it must stay non-empty wherever a forecast exists.
 */
function qualityLabel(route: RecurringRouteOut): string {
    if (!route.hasGeometry) return "Route wird berechnet …";
    // A thumbnail computed for a departure that has since passed describes the wrong ride.
    const thumbnail = liveThumbnail(route);
    // The server scores the worst sample; no label means nothing could be scored yet.
    return thumbnail?.rideLabel ?? "Noch keine Prognose";
}

function profileLabel(profile: string): string {
    const labels: Record<string, string> = {
        bike: "Velo",
        ebike: "E-Bike",
        fast_ebike: "S-Pedelec",
    };
    return labels[profile] ?? profile;
}

const addButtonLabel = computed(() => (atRouteLimit.value ? "Tarifgrenze erreicht" : "Route hinzufügen"));
</script>

<template>
    <q-card
        :square="$q.screen.lt['sm']"
        :flat="$q.screen.lt['sm']"
        class="q-ma-xs-none q-ma-sm-md col-sm-6 col-md-8 col column"
    >
        <q-card-section v-if="!loading && routes.length" class="no-padding">
            <q-item-label header class="no-padding">
                <q-btn
                    flat
                    dense
                    label="Route hinzufügen"
                    no-caps
                    :icon="symSharpAdd"
                    :disable="atRouteLimit"
                    :title="addButtonLabel"
                    @click="emit('add')"
                >
                    <q-tooltip>{{ addButtonLabel }}</q-tooltip>
                </q-btn>
            </q-item-label>
            <q-separator />
        </q-card-section>
        <q-card-section v-if="loading" class="text-center q-my-auto">
            <q-spinner-dots size="2rem" />
        </q-card-section>
        <q-card-section v-else-if="!routes.length" class="text-center q-my-auto">
            <q-icon :name="symSharpRoute" size="3rem" />
            <q-item-label class="q-my-md">Noch keine Routen — leg los!</q-item-label>
            <q-btn color="primary" label="Route erstellen" @click="emit('add')" />
        </q-card-section>
        <q-card-section v-else class="q-pa-none col">
            <q-list separator class="col column">
                <!-- At the tier limit: say so where the add button just went dead. -->
                <q-item v-if="atRouteLimit" class="bg-grey-2 text-caption">
                    <q-item-section>
                        {{ maxRoutes }} von {{ maxRoutes }} Routen belegt — Pro hebt das Limit auf.
                    </q-item-section>
                    <q-item-section side>
                        <q-btn dense flat color="primary" label="Upgrade" @click="emit('upgrade')" />
                    </q-item-section>
                </q-item>

                <!-- Route items -->
                <q-item
                    v-for="route in routes"
                    :key="route.id"
                    v-ripple
                    :to="`/routes/${route.id}`"
                    class="q-py-sm q-pl-sm q-pr-none"
                >
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
                            @click.stop.prevent="emit('delete', route.id)"
                        />
                    </q-item-section>
                </q-item>
            </q-list>
        </q-card-section>
    </q-card>
</template>

<style scoped></style>
