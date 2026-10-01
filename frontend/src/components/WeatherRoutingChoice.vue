<script setup lang="ts">
import { symSharpLock } from "@quasar/extras/material-symbols-sharp";
import { useI18n } from "vue-i18n";
import { tp } from "@/i18n";
import { useEntitlements } from "@/composables/useEntitlements";

/**
 * Whether to ride around bad weather: always the rider's own choice, off until they switch it
 * on, and a Plus feature. Without Plus the switches are shown locked and off; the server stores
 * them off for such accounts either way.
 */
const avoidRain = defineModel<boolean>("avoidRain", { required: true });
const avoidHeadwind = defineModel<boolean>("avoidHeadwind", { required: true });
/** Stay in the sun: the server weighs clouds, and GraphHopper the terrain and trees towards the sun. */
const avoidShade = defineModel<boolean>("avoidShade", { required: true });
/** Off for hiking: a headwind barely slows a walker, so the server never routes around one. */
const { headwind, profile = "bike" } = defineProps<{ headwind: boolean; profile?: string }>();

const { t } = useI18n();
const { weatherRouting } = useEntitlements();
</script>

<template>
    <div data-testid="weather-routing-choice">
        <div class="row items-center q-gutter-x-sm">
            <span class="text-caption">{{ tp(profile, "weatherRouting.title") }}</span>
            <q-badge v-if="!weatherRouting" color="accent" label="Plus" />
        </div>
        <q-toggle
            :model-value="weatherRouting && avoidRain"
            :disable="!weatherRouting"
            :label="t('weatherRouting.avoidRain')"
            @update:model-value="avoidRain = $event"
        />
        <q-toggle
            v-if="headwind"
            :model-value="weatherRouting && avoidHeadwind"
            :disable="!weatherRouting"
            :label="t('weatherRouting.avoidHeadwind')"
            @update:model-value="avoidHeadwind = $event"
        />
        <q-toggle
            :model-value="weatherRouting && avoidShade"
            :disable="!weatherRouting"
            :label="t('weatherRouting.avoidShade')"
            @update:model-value="avoidShade = $event"
        />
        <div v-if="weatherRouting && avoidShade" class="text-caption text-muted q-mb-xs">
            {{ tp(profile, "weatherRouting.shadeExplanation") }}
        </div>
        <div v-if="weatherRouting" class="text-caption text-muted">
            {{ tp(profile, "weatherRouting.explanation") }}
        </div>
        <div v-else class="row items-center no-wrap q-gutter-x-xs text-caption text-muted">
            <q-icon :name="symSharpLock" />
            <span>
                {{ tp(profile, "weatherRouting.plusOnly") }}
                <router-link to="/account">{{ t("weatherRouting.seePlus") }}</router-link>
            </span>
        </div>
    </div>
</template>
