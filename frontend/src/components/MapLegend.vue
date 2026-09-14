<script setup lang="ts">
import { computed } from "vue";
import { QIcon, QTooltip } from "quasar";
import { symSharpInfo } from "@quasar/extras/material-symbols-sharp";

import { NO_DATA_COLOR, SPECTRAL_10 } from "@/utils/rideQuality";

defineProps<{
    /** Show the grey swatch - only when some stretch really has no usable data. */
    showNoData?: boolean;
}>();

const barGradient = computed(() => `linear-gradient(to right, ${SPECTRAL_10.join(", ")})`);
</script>

<template>
    <div class="wx-legend bg-tint-neutral">
        <div class="wx-legend__head">
            <span class="wx-legend__caption">Fahrqualität</span>
            <QIcon :name="symSharpInfo" size="14px" class="wx-legend__info" />
            <QTooltip anchor="top middle" self="bottom middle" max-width="240px">
                Aus Regen (55 %), Gegenwind (25 %) und Temperatur (20 %) berechnet. Ein Richtwert ohne Einheit - die
                genauen Werte stehen in den Wetterpunkten auf der Karte.
            </QTooltip>
        </div>
        <div class="wx-legend__bar" :style="{ background: barGradient }"></div>
        <div class="wx-legend__scale">
            <span>gut</span>
            <span>schlecht</span>
        </div>
        <div v-if="showNoData" class="wx-legend__nodata">
            <span class="wx-legend__swatch" :style="{ background: NO_DATA_COLOR }"></span>
            <span>Keine Daten</span>
        </div>
    </div>
</template>

<style scoped>
.wx-legend {
    padding: 6px 8px 5px;
    border-radius: 6px;
    box-shadow: 0 1px 4px rgb(0 0 0 / 25%);
    font: 11px/1.3 var(--app-font);
    color: var(--q-text-muted);
    user-select: none;
}

.wx-legend__head {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-bottom: 4px;
}

.wx-legend__caption {
    font-weight: 600;
}

.wx-legend__info {
    cursor: help;
    opacity: 0.7;
}

.wx-legend__bar {
    width: 132px;
    height: 8px;
    border-radius: 2px;
}

.wx-legend__scale,
.wx-legend__nodata {
    display: flex;
    justify-content: space-between;
    margin-top: 2px;
}

.wx-legend__nodata {
    justify-content: flex-start;
    align-items: center;
    gap: 4px;
    margin-top: 4px;
}

.wx-legend__swatch {
    width: 12px;
    height: 8px;
    border-radius: 2px;
    flex: none;
}

/* The saved-route detail tile is a small square - drop the caption there rather than
   letting the legend eat the map. */
@container (max-width: 320px) {
    .wx-legend__caption {
        display: none;
    }

    .wx-legend__bar {
        width: 96px;
    }
}
</style>
