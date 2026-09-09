<script setup lang="ts">
import {
    symSharpAdd,
    symSharpCloud,
    symSharpCloudOff,
    symSharpDelete,
    symSharpRoute,
} from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api";

defineProps<{
    routes: RecurringRouteOut[];
    loading: boolean;
}>();

const emit = defineEmits<{
    add: [];
    delete: [id: string];
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

function profileLabel(profile: string): string {
    const labels: Record<string, string> = {
        bike: "Velo",
        ebike: "E-Bike",
        fast_ebike: "S-Pedelec",
        car: "Auto",
        foot: "Fuss",
    };
    return labels[profile] || profile;
}
</script>

<template>
    <q-list bordered separator class="full-height">
        <q-item-label header class="row items-center justify-between">
            <span>Routen</span>
            <q-btn flat round dense :icon="symSharpAdd" @click="emit('add')" title="Route hinzufügen" />
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

        <!-- Route items -->
        <q-item v-for="route in routes" :key="route.id" v-ripple :to="`/routes/${route.id}`">
            <q-item-section avatar>
                <q-icon :name="route.forecastAvailable ? symSharpCloud : symSharpCloudOff" />
            </q-item-section>
            <q-item-section>
                <q-item-label>{{ route.name }}</q-item-label>
                <q-item-label caption>
                    {{ relativeTime(route.nextDeparture) }}
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
