<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { RouteForecastOut } from "@norain/api/models";
import { metricLabels, rangeText, swissTime } from "@/utils/forecastDetails";
import { useEntitlements } from "@/composables/useEntitlements";

const props = defineProps<{ forecast: RouteForecastOut; selectedSample: number; showChartKey?: boolean }>();
const emit = defineEmits<{ "update:selectedSample": [index: number] }>();
const { t } = useI18n();
const expanded = ref(false);
const metrics = computed(() => metricLabels());
const sample = computed(() => props.forecast.samples[props.selectedSample]);
const uncertainty = computed(() => sample.value?.uncertainty);

const { entitlements } = useEntitlements();
/**
 * The spread is withheld from the free tier rather than missing.
 *
 * Those are different statements and the UI must not conflate them: "Nicht verfügbar"
 * claims we have no data, which would be a lie here. Only say this when the server
 * actually reports the tier — never infer a paywall from absent data.
 */
const uncertaintyLocked = computed(() => entitlements.value?.ensembleUncertainty === false);
const partial = computed(() => props.forecast.uncertaintyPartial);
</script>

<template>
    <q-expansion-item v-model="expanded" dense class="forecast-details q-mb-sm" data-testid="forecast-details">
        <template #header>
            <q-item-section side class="">
                <q-item-label overline class="">{{ t("details.title") }}</q-item-label>
                <!--                <div class="row items-center q-gutter-x-sm">-->
                <!--                    <span class="text-caption text-primary">Details</span>-->
                <!--                    <span v-if="showChartKey" class="text-caption text-muted">-->
                <!--                        Fläche: erwarteter Bereich. Linie auswählen für Details.-->
                <!--                    </span>-->
                <!--                </div>-->
            </q-item-section>
        </template>
        <div class="q-pa-md">
            <p class="text-caption">{{ t("details.bandExplained") }}</p>
            <q-banner v-if="uncertaintyLocked" dense class="bg-tint-warn q-mb-md">
                {{ t("details.uncertaintyLocked") }}
                <template #action>
                    <q-btn flat dense color="primary" :label="t('quota.upgrade')" to="/account" />
                </template>
            </q-banner>
            <q-banner v-else-if="partial" dense class="bg-tint-warn q-mb-md">
                {{ t("details.uncertaintyPartial") }}
            </q-banner>
            <template v-if="sample">
                <label class="text-weight-medium">
                    {{
                        t("details.point", {
                            time: t("common.clock", { time: swissTime(sample.eta) }),
                            minutes: Math.round(sample.elapsedS / 60),
                        })
                    }}
                    <input
                        class="sample-slider"
                        type="range"
                        min="0"
                        :max="forecast.samples.length - 1"
                        step="1"
                        :value="selectedSample"
                        :aria-label="t('details.pointLabel')"
                        :aria-valuetext="t('common.clock', { time: swissTime(sample.eta) })"
                        @input="emit('update:selectedSample', Number(($event.target as HTMLInputElement).value))"
                    />
                </label>
                <p class="text-caption">
                    {{
                        t("details.pointRisk", {
                            risk: sample.pop == null ? t("common.notAvailable") : `${Math.round(sample.pop * 100)}%`,
                        })
                    }}
                </p>
                <p v-if="!uncertainty && !uncertaintyLocked">{{ t("details.noRange") }}</p>
                <p v-if="sample.rainRateMmH != null" class="text-caption">
                    {{
                        t("details.rain", {
                            rate: sample.rainRateMmH.toFixed(1),
                            mm: sample.rainMm.toFixed(1),
                            minutes: (sample.precipitationIntervalS ?? 3600) / 60,
                        })
                    }}
                </p>
                <p v-if="uncertainty?.rainIfWet != null" class="text-caption">
                    {{
                        uncertainty.pop === 0
                            ? t("details.probablyDry")
                            : t("details.ifWet", { rate: uncertainty.rainIfWet.toFixed(1) })
                    }}
                </p>
                <dl v-if="!uncertaintyLocked" class="metric-grid">
                    <template v-for="metric in metrics" :key="metric.key">
                        <dt>{{ metric.label }}</dt>
                        <dd>
                            {{ rangeText(uncertainty?.metrics[metric.key], metric.unit) }}
                        </dd>
                    </template>
                </dl>
                <p class="text-caption">{{ t("details.headwindSign") }}</p>
            </template>
            <p v-else>{{ t("details.noData") }}</p>
        </div>
    </q-expansion-item>
</template>

<style scoped>
.sample-slider {
    display: block;
    width: 100%;
    min-height: 44px;
    accent-color: var(--q-primary);
}
.metric-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 8px;
}
dd {
    margin: 0;
}
dt,
dd {
    min-width: 0;
    overflow-wrap: anywhere;
}
</style>
