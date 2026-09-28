<script setup lang="ts">
import { symSharpLock } from "@quasar/extras/material-symbols-sharp";
import { useI18n } from "vue-i18n";
import { useEntitlements } from "@/composables/useEntitlements";

/**
 * Whether to ride around bad weather: always the rider's own choice, off until they switch it
 * on, and a Plus feature. Without Plus the switches are shown locked and off; the server stores
 * them off for such accounts either way.
 */
const avoidRain = defineModel<boolean>("avoidRain", { required: true });
const avoidHeadwind = defineModel<boolean>("avoidHeadwind", { required: true });

const { t } = useI18n();
const { weatherRouting } = useEntitlements();
</script>

<template>
    <div data-testid="weather-routing-choice">
        <div class="row items-center q-gutter-x-sm">
            <span class="text-caption">{{ t("weatherRouting.title") }}</span>
            <q-badge v-if="!weatherRouting" color="accent" label="Plus" />
        </div>
        <q-toggle
            :model-value="weatherRouting && avoidRain"
            :disable="!weatherRouting"
            :label="t('weatherRouting.avoidRain')"
            @update:model-value="avoidRain = $event"
        />
        <q-toggle
            :model-value="weatherRouting && avoidHeadwind"
            :disable="!weatherRouting"
            :label="t('weatherRouting.avoidHeadwind')"
            @update:model-value="avoidHeadwind = $event"
        />
        <div v-if="weatherRouting" class="text-caption text-muted">
            {{ t("weatherRouting.explanation") }}
        </div>
        <div v-else class="row items-center no-wrap q-gutter-x-xs text-caption text-muted">
            <q-icon :name="symSharpLock" />
            <span>
                {{ t("weatherRouting.plusOnly") }}
                <router-link to="/account">{{ t("weatherRouting.seePlus") }}</router-link>
            </span>
        </div>
    </div>
</template>
