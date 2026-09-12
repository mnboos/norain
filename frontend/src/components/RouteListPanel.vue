<script setup lang="ts">
import { symSharpAdd, symSharpDelete, symSharpRoute } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";

import RouteThumbnail from "@/components/RouteThumbnail.vue";
import { rideScore, rideScoreLabel } from "@/utils/rideQuality";

withDefaults(
    defineProps<{
        routes: RecurringRouteOut[];
        loading: boolean;
        /** Quota state, from useEntitlements. Server-enforced; this only shapes the UI. */
        atRouteLimit?: boolean;
        maxRoutes?: number | null;
    }>(),
    { atRouteLimit: false, maxRoutes: null },
);

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
 * The ride-quality wording for a route. The glyph beside it is deliberately shape-only, so
 * this caption is the *sole* channel carrying the forecast in the list - it must never be
 * dropped to save a line, and it must stay non-empty wherever a forecast exists.
 */
function qualityLabel(route: RecurringRouteOut): string {
    if (!route.hasGeometry) return "Route wird berechnet …";
    const thumbnail = route.thumbnail;
    // A thumbnail computed for a departure that has since passed describes the wrong ride.
    if (!thumbnail || (thumbnail.departure && thumbnail.departure !== route.nextDeparture)) {
        return "Noch keine Prognose";
    }
    const scored = (thumbnail.samples ?? []).flatMap(s => (s ? (rideScore(s) ?? []) : []));
    if (!scored.length) return "Noch keine Prognose";
    return rideScoreLabel(scored.reduce((a, b) => (b.score > a.score ? b : a)));
}

function profileLabel(profile: string): string {
    const labels: Record<string, string> = {
        bike: "Velo",
        ebike: "E-Bike",
        fast_ebike: "S-Pedelec",
        car: "Auto",
        foot: "Fuss",
    };
    return labels[profile] ?? profile;
}
</script>

<template>
    <q-list bordered separator class="full-height">
        <q-item-label header class="row items-center justify-between">
            <span>Routen</span>
            <q-btn
                flat
                round
                dense
                :icon="symSharpAdd"
                :disable="atRouteLimit"
                :title="atRouteLimit ? 'Tarifgrenze erreicht' : 'Route hinzufügen'"
                @click="emit('add')"
            />
        </q-item-label>

        <!-- Loading -->
        <div v-if="loading" class="q-pa-md text-center">
            <q-spinner-dots size="2rem" />
        </div>

        <!-- Empty -->
        <div v-else-if="!routes.length" class="q-pa-md text-center text-grey">
            <q-icon :name="symSharpRoute" size="3rem" />
            <p class="q-mt-sm">Noch keine Routen — leg los!</p>
            <q-btn color="primary" label="Route erstellen" @click="emit('add')" />
        </div>

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
        <q-item v-for="route in routes" :key="route.id" v-ripple :to="`/routes/${route.id}`">
            <q-item-section avatar>
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
</template>

<style scoped>
.full-height {
    height: 100%;
}
</style>
