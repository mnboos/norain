<script setup lang="ts">
import { computed, ref, toRefs } from "vue";
import { useI18n } from "vue-i18n";
import {
    symSharpAcUnit,
    symSharpAir,
    symSharpRainy,
    symSharpSchedule,
    symSharpSpeed,
    symSharpStraighten,
    symSharpThermostat,
    symSharpWaterDrop,
} from "@quasar/extras/material-symbols-sharp";
import type { RouteForecastOut } from "@norain/api/models";
import { meanFeltTemp, peakRain } from "@/utils/forecastDetails";
import { hasWindEffort } from "@/utils/bikeProfiles";
import { headwindColor, temperatureColor } from "@/utils/statColors";
import { impactText, windEffortText } from "@/utils/levels";

const props = withDefaults(
    defineProps<{
        forecast: RouteForecastOut;
        /**
         * Tiles per row. Quasar's col-* classes follow the screen width, not the card's, so
         * the parent - which knows how wide it made the card - picks this.
         */
        columns?: 2 | 3 | 4;
    }>(),
    { columns: 3 },
);
const { forecast } = toRefs(props);
const { t } = useI18n();
const showExplanation = ref(false);

/** The colour of a tile with nothing to flag. */
const NEUTRAL = "blue-grey-6";

const peakRate = computed(() => peakRain(forecast.value));
const feltTemp = computed(() => meanFeltTemp(forecast.value.samples));
// A hike has no wind effort (see hasWindEffort); its tile shows the strongest gust instead.
const maxGust = computed(() => {
    const gusts = forecast.value.samples.map(s => s.windGust).filter((g): g is number => g != null);
    return gusts.length ? Math.round(Math.max(...gusts)) : null;
});
const stats = computed(() => [
    {
        label: t("keyData.felt"),
        icon: symSharpThermostat,
        color: temperatureColor(feltTemp.value) ?? NEUTRAL,
        value: feltTemp.value == null ? null : Math.round(feltTemp.value),
        unit: "°C",
    },
    {
        label: t("badges.rainRisk"),
        icon: symSharpRainy,
        color: "blue-grey-6",
        value:
            forecast.value.summary.rainProbability == null
                ? null
                : Math.round(forecast.value.summary.rainProbability * 100),
        unit: "%",
    },
    hasWindEffort(forecast.value.profile)
        ? {
              label: t("keyData.windEffort"),
              icon: symSharpSpeed,
              color: "blue-grey-6",
              value: windEffortText(forecast.value.summary.maxWindEffortLevel) || null,
              unit: "",
          }
        : {
              label: t("keyData.maxGust"),
              icon: symSharpAir,
              color: "blue-grey-6",
              value: maxGust.value,
              unit: "km/h",
          },
    {
        label: t("randomForm.length.distance"),
        icon: symSharpStraighten,
        color: "blue-grey-6",
        value: (forecast.value.totalDistanceM / 1000).toFixed(1),
        unit: "km",
    },

    {
        // "kein" and "Nicht verfügbar" are different answers: the first is the forecast
        // saying the road is fine, the second is having no forecast to read.
        label: t("badges.frost"),
        icon: symSharpAcUnit,
        // Blue only when the server found a frost risk somewhere on the ride.
        color: forecast.value.summary.maxFrostLevel != null ? "light-blue-6" : NEUTRAL,
        value: forecast.value.samples.length
            ? impactText(forecast.value.summary.maxFrostLevel) || t("keyData.noFrost")
            : null,
        unit: "",
    },

    {
        label: t("keyData.rainMax"),
        icon: symSharpWaterDrop,
        color: Number(peakRate.value) > 0 ? "light-blue-7" : NEUTRAL,
        value: peakRate.value,
        unit: "mm/h",
    },

    {
        label: t("keyData.headwindMax"),
        icon: symSharpAir,
        color: headwindColor(forecast.value.summary.maxHeadwind) ?? NEUTRAL,
        value: forecast.value.summary.maxHeadwind,
        unit: "km/h",
    },
    {
        label: t("keyData.duration"),
        icon: symSharpSchedule,
        color: "blue-grey-6",
        value: Math.round(forecast.value.totalSeconds / 60),
        unit: "min",
    },
]);
/** The stats cut into table rows of `columns` cells. */
const rows = computed(() =>
    Array.from({ length: Math.ceil(stats.value.length / props.columns) }, (_, i) =>
        stats.value.slice(i * props.columns, (i + 1) * props.columns),
    ),
);

</script>

<template>
    <q-card>
        <!-- The grid runs to the card's edges; separators draw the lines between the cells. -->
        <q-card-section :aria-label="t('keyData.label')" class="q-pa-none">
            <template v-for="(row, r) in rows" :key="r">
                <div class="row no-wrap">
                    <template v-for="(stat, c) in row" :key="stat.label">
                        <q-separator v-if="c > 0" vertical />
                        <q-item class="col column flex-center text-center q-px-xs">
                            <!-- Quasar's color prop takes palette names only; a hex goes in style. -->
                            <q-icon
                                :name="stat.icon"
                                size="sm"
                                :color="stat.color.startsWith('#') ? undefined : stat.color"
                                :style="stat.color.startsWith('#') ? { color: stat.color } : undefined"
                            />
                            <q-item-label caption>{{ stat.label }}</q-item-label>
                            <q-item-label class="text-weight-bold">
                                <template v-if="stat.value != null">
                                    {{ stat.value }}
                                    <span v-if="stat.unit">{{ stat.unit }}</span>
                                </template>
                                <template v-else>{{ t("common.notAvailable") }}</template>
                            </q-item-label>
                        </q-item>
                    </template>
                    <!-- Keep a short last row's cells as wide as the others. -->
                    <template v-for="c in columns - row.length" :key="`empty-${c}`">
                        <q-separator vertical />
                        <div class="col" />
                    </template>
                </div>
            </template>
        </q-card-section>
        <template v-if="forecast.samples.some(s => s.pop == null)">
            <q-separator />
            <q-card-section class="text-caption text-muted">
                {{ t("keyData.riskPartial") }}
            </q-card-section>
        </template>
        <q-dialog v-model="showExplanation">
            <q-card>
                <q-card-section class="text-body2">{{ t("keyData.note") }}</q-card-section>
                <q-card-actions align="right">
                    <q-btn v-close-popup flat :label="t('common.close')" color="primary" />
                </q-card-actions>
            </q-card>
        </q-dialog>
    </q-card>
</template>
