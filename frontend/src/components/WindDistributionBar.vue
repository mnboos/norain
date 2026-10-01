<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { tp } from "@/i18n";
import type { WindDistribution } from "@norain/api/models";
import { windDistributionParts } from "@/utils/wind";

const props = defineProps<{ distribution: WindDistribution; profile?: string | null }>();
const { t } = useI18n();
// Reads the locale through t(), so the labels follow a switch.
const parts = computed(() => windDistributionParts(props.distribution));
const total = computed(() => parts.value.reduce((sum, p) => sum + p.meters, 0));
const description = computed(() => parts.value.map(p => `${(p.meters / 1000).toFixed(1)} km ${p.label}`).join(" · "));
const colors = ["negative", "primary", "secondary", "blue-grey-3", "grey-7"];
</script>

<template>
    <q-card flat class="text-caption" data-testid="wind-distribution">
        <!-- The card around this names it ("Wind entlang der Strecke"). -->
        <div class="row items-center q-gutter-x-sm">
            <span v-if="total > 0 && distribution.meanFeltSpeed != null" class="text-muted">
                {{ t("windBar.meanFelt", { speed: distribution.meanFeltSpeed.toFixed(1) }) }}
                <span v-if="distribution.feltCoveredM < total - 0.01">
                    · {{ t("windBar.coveredOn", { km: (distribution.feltCoveredM / 1000).toFixed(1) }) }}
                </span>
                <span v-if="distribution.timingSource === 'sample-interpolation'">· {{ tp(profile, "windBar.approxPace") }}</span>
            </span>
            <span v-else-if="total > 0" class="text-muted">{{ t("windBar.feltUnavailable") }}</span>
        </div>
        <template v-if="total > 0">
            <div class="row no-wrap rounded-borders overflow-hidden q-my-xs" role="img" :aria-label="description">
                <q-linear-progress
                    v-for="(part, index) in parts"
                    :key="part.label"
                    :value="1"
                    :color="colors[index]"
                    size="8px"
                    :style="{ width: `${(100 * part.meters) / total}%` }"
                    aria-hidden="true"
                />
            </div>
            <div class="row q-gutter-x-md text-muted">
                <template v-for="(part, index) in parts" :key="part.label">
                    <span v-if="part.meters > 0">
                        <q-badge :color="colors[index]" class="q-mr-xs" />
                        {{ (part.meters / 1000).toFixed(1) }} km {{ part.label }}
                    </span>
                </template>
            </div>
        </template>
        <div v-else>{{ t("windBar.noRoute") }}</div>
    </q-card>
</template>
