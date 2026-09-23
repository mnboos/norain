<route lang="json5">
{
    name: "journey-detail",
    meta: { title: "Reise", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuasar } from "quasar";
import { useRoute } from "vue-router";
import { symSharpArrowBack, symSharpEdit, symSharpRefresh } from "@quasar/extras/material-symbols-sharp";
import type { JourneyIn } from "@norain/api/models";
import JourneyDayPanel from "@/components/journey/JourneyDayPanel.vue";
import JourneyFormDialog from "@/components/journey/JourneyFormDialog.vue";
import { useJourney, useReplanJourney, useUpdateJourney } from "@/queries/journeys";
import { dayLabel, duration, km, planStatusLabel } from "@/utils/journeys";

const $q = useQuasar();
const currentRoute = useRoute();
const journeyId = computed(() => String(currentRoute.params.id));
const { data: journey, isLoading, error } = useJourney(journeyId);

const days = computed(() => journey.value?.days ?? []);
const planning = computed(() => !!journey.value && !["done", "failed"].includes(journey.value.planStatus));
const selectedDay = ref(0);
watch(journeyId, () => {
    selectedDay.value = 0;
});
const day = computed(() => days.value[selectedDay.value]);

function dayStats(index: number): string {
    const stages = days.value[index]?.stages ?? [];
    const chosen = stages.find(s => s.recommended) ?? stages[0];
    return chosen ? `${km(chosen.distanceM)} · ${duration(chosen.totalSeconds)}` : "";
}

const editing = ref(false);
const update = useUpdateJourney();
const replan = useReplanJourney();
function onSave(data: JourneyIn) {
    update.mutate(
        { id: journeyId.value, data },
        { onError: () => $q.notify({ type: "negative", message: "Reise konnte nicht gespeichert werden." }) },
    );
}
</script>

<template>
    <q-page class="q-pa-md column">
        <div class="row items-center q-mb-md no-wrap">
            <q-btn flat round dense :icon="symSharpArrowBack" to="/journeys" aria-label="Zurück" />
            <div v-if="journey" class="col q-ml-sm ellipsis">
                <h1 class="text-h6 text-weight-bold q-my-none ellipsis">{{ journey.name }}</h1>
                <div class="text-caption text-muted ellipsis">{{ journey.startName }} → {{ journey.destName }}</div>
            </div>
            <template v-if="journey">
                <q-btn flat dense no-caps :icon="symSharpEdit" label="Bearbeiten" @click="editing = true" />
                <q-btn
                    flat
                    dense
                    no-caps
                    :icon="symSharpRefresh"
                    label="Neu planen"
                    :loading="replan.isPending.value"
                    :disable="planning"
                    @click="replan.mutate(journeyId)"
                />
            </template>
        </div>

        <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>
        <q-banner v-else-if="error || !journey" class="bg-tint-error" rounded>Reise nicht gefunden.</q-banner>

        <template v-else>
            <q-banner v-if="planning" rounded class="bg-tint-warn q-mb-md">
                <template #avatar><q-spinner-dots size="1.5rem" color="accent" /></template>
                {{ planStatusLabel(journey.planStatus) }}… Etappen, Pausen und Unterkünfte werden gesucht.
            </q-banner>
            <q-banner v-else-if="journey.planStatus === 'failed'" rounded class="bg-tint-error q-mb-md">
                {{ journey.planError || "Die Reise konnte nicht geplant werden." }}
            </q-banner>

            <template v-if="days.length">
                <q-tabs v-model="selectedDay" dense align="left" outside-arrows mobile-arrows class="q-mb-md">
                    <q-tab v-for="(d, i) in days" :key="d.id" :name="i" no-caps>
                        <div class="text-weight-medium">Tag {{ i + 1 }} · {{ dayLabel(d.date) }}</div>
                        <div class="text-caption text-muted">{{ dayStats(i) }}</div>
                    </q-tab>
                </q-tabs>
                <JourneyDayPanel v-if="day" :key="day.id" :journey="journey" :day="day" class="col" />
            </template>
        </template>

        <JourneyFormDialog v-if="journey" v-model="editing" :journey="journey" @save="onSave" />
    </q-page>
</template>
