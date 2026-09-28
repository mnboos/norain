<script setup lang="ts">
import { computed, ref, toRefs } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import type { RouteForecastOut } from "@norain/api/models";
import { forecastHeadline } from "@/utils/forecastDetails";
import WeatherGlyph from "@/components/WeatherGlyph.vue";
import WeatherSections from "@/components/WeatherSections.vue";
import { symSharpInfo } from "@quasar/extras/material-symbols-sharp";

const props = defineProps<{ forecast: RouteForecastOut }>();
const { forecast } = toRefs(props);
// Beside the text on a wide card; above it on a narrow one, where both would be squeezed.
const $q = useQuasar();
const { t } = useI18n();
const horizontal = computed(() => $q.screen.width >= 1280 || $q.screen.lt.md);

const headline = computed(() => forecastHeadline(forecast.value.summary, forecast.value.samples));
const explanation = computed(() => {
    if (!forecast.value.samples.length) return t("summaryCard.noData");
    const p = forecast.value.summary.rainProbability;
    if (p == null) return t("summaryCard.riskUnavailable");
    if (p < 0.1) return "";
    return t("summaryCard.peakRisk", { percent: Math.round(p * 100) });
});

// The sample that spoils the ride most (the server's highest ride score, as in the route
// list), so the big glyph shows the worst weather of the ride. Without scores, the start.
const worstSample = computed(() => {
    const samples = forecast.value.samples;
    let worst = samples[0];
    for (const s of samples) {
        if (s.rideScore != null && (worst?.rideScore == null || s.rideScore > worst.rideScore)) worst = s;
    }
    return worst;
});
const showExplanation = ref(false);

</script>

<template>
    <q-card class="column">
        <q-card-section class="q-pb-none row">
            <div class="text-subtitle2">{{ t("summaryCard.title") }}</div>
            <q-space />
            <q-btn
                flat
                dense
                round
                size="sm"
                :icon="symSharpInfo"
                :aria-label="t('summaryCard.explain')"
                @click="showExplanation = true"
            />
        </q-card-section>
        <q-card-section :horizontal="horizontal" class="q-my-auto">
            <q-card-section v-if="worstSample" class="col-auto flex flex-center" :class="{ 'q-pb-none': !horizontal }">
                <WeatherGlyph
                    :weather-code="worstSample.weatherCode"
                    :rain-mm="worstSample.rainMm"
                    :eta="worstSample.eta"
                    :label="headline"
                    size="88px"
                />
            </q-card-section>
            <q-card-section class="col">
                <h5 class="text-weight-medium q-mb-sm">{{ headline }}</h5>
                <p v-if="explanation" class="text-body2 q-mb-sm">{{ explanation }}</p>
                <WeatherSections v-if="forecast.sections?.length" :sections="forecast.sections" />
            </q-card-section>
        </q-card-section>
        <q-dialog v-model="showExplanation">
            <q-card>
                <q-card-section class="text-body2">{{ t("summaryCard.note") }}</q-card-section>
                <q-card-actions align="right">
                    <q-btn v-close-popup flat :label="t('common.close')" color="primary" />
                </q-card-actions>
            </q-card>
        </q-dialog>
    </q-card>
</template>
