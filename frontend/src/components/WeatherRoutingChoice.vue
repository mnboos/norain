<script setup lang="ts">
import { symSharpLock } from "@quasar/extras/material-symbols-sharp";
import { useEntitlements } from "@/composables/useEntitlements";

/**
 * Whether to ride around bad weather: always the rider's own choice, off until they switch it
 * on, and a Plus feature. Without Plus the switches are shown locked and off; the server stores
 * them off for such accounts either way.
 */
const avoidRain = defineModel<boolean>("avoidRain", { required: true });
const avoidHeadwind = defineModel<boolean>("avoidHeadwind", { required: true });

const { weatherRouting } = useEntitlements();
</script>

<template>
    <div data-testid="weather-routing-choice">
        <div class="row items-center q-gutter-x-sm">
            <span class="text-caption">Um schlechtes Wetter herum fahren</span>
            <q-badge v-if="!weatherRouting" color="accent" label="Plus" />
        </div>
        <q-toggle
            :model-value="weatherRouting && avoidRain"
            :disable="!weatherRouting"
            label="Regen ausweichen"
            @update:model-value="avoidRain = $event"
        />
        <q-toggle
            :model-value="weatherRouting && avoidHeadwind"
            :disable="!weatherRouting"
            label="Starken Gegenwind meiden"
            @update:model-value="avoidHeadwind = $event"
        />
        <div v-if="weatherRouting" class="text-caption text-muted">
            Für Fahrten in den nächsten drei Tagen legt Meteolane die Strecke dorthin, wo es trocken ist und der Wind
            weniger bläst, zur Zeit, zu der du dort bist. Aus: die Strecke folgt nur deinen Wünschen an die Strasse.
        </div>
        <div v-else class="row items-center no-wrap q-gutter-x-xs text-caption text-muted">
            <q-icon :name="symSharpLock" />
            <span>
                Mit Plus kann Meteolane die Strecke um Regen und Gegenwind herum legen.
                <router-link to="/account">Plus ansehen</router-link>
            </span>
        </div>
    </div>
</template>
