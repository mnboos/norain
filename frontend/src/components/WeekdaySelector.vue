<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { intlLocale } from "@/i18n";
import { weekdayLabels } from "@/utils/weeklySchedule";

/** Cron weekdays: Monday = 1 … Sunday = 7. An empty selection remains invalid in the parent form. */
const days = defineModel<number[]>({ required: true });
const { t } = useI18n();
const labels = computed(() => weekdayLabels());
const fullLabels = computed(() => {
    const formatter = new Intl.DateTimeFormat(intlLocale(), { weekday: "long", timeZone: "UTC" });
    return Array.from({ length: 7 }, (_, index) => formatter.format(new Date(Date.UTC(2024, 0, index + 1))));
});
function toggle(day: number) {
    days.value = days.value.includes(day)
        ? days.value.filter(value => value !== day)
        : [...days.value, day].sort((a, b) => a - b);
}
</script>

<template>
    <div class="weekday-selector" role="group" :aria-label="t('routeForm.days')">
        <q-btn
            v-for="(label, index) in labels"
            :key="index"
            class="weekday-tile"
            :class="{ 'weekday-tile--selected': days.includes(index + 1) }"
            type="button"
            unelevated
            no-caps
            :label="label"
            :aria-label="fullLabels[index]"
            :aria-pressed="days.includes(index + 1)"
            @click="toggle(index + 1)"
        />
    </div>
</template>

<style scoped>
.weekday-selector {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: clamp(4px, 1.2vw, 10px);
}
.weekday-tile {
    min-width: 0;
    min-height: 44px;
    padding: 8px 0;
    border-radius: 10px;
    background: #f2f5f8;
    color: #65768a;
    font-size: 14px;
    font-weight: 500;
}
.weekday-tile--selected {
    background: var(--q-primary);
    color: white;
}
.weekday-tile:focus-visible {
    outline: 2px solid var(--q-primary);
    outline-offset: 3px;
}
.body--dark .weekday-tile:not(.weekday-tile--selected) {
    background: #232c3a;
    color: #b6c3d2;
}
@media (max-width: 399px) {
    .weekday-tile {
        font-size: 12px;
    }
}
</style>
