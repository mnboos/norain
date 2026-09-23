<route lang="json5">
{
    name: "journeys",
    meta: { title: "Reisen", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { ref } from "vue";
import { useQuasar } from "quasar";
import { useRouter } from "vue-router";
import { symSharpAdd, symSharpArrowBack, symSharpDelete, symSharpLuggage } from "@quasar/extras/material-symbols-sharp";
import type { JourneyIn, JourneyOut } from "@norain/api/models";
import JourneyFormDialog from "@/components/journey/JourneyFormDialog.vue";
import { isQuotaExceeded } from "@/services/http";
import { useCreateJourney, useDeleteJourney, useJourneys } from "@/queries/journeys";
import { journeyDates, planStatusLabel } from "@/utils/journeys";

const $q = useQuasar();
const router = useRouter();
const showForm = ref(false);

const { data: journeys, isLoading } = useJourneys();
const createMutation = useCreateJourney();
const deleteMutation = useDeleteJourney();

function onSave(data: JourneyIn) {
    createMutation.mutate(data, {
        onSuccess: journey => void router.push(`/journeys/${journey.id}`),
        onError: (err: unknown) => {
            if (isQuotaExceeded(err)) {
                $q.dialog({
                    title: "Tarifgrenze erreicht",
                    message: "Dein Tarif erlaubt eine Reise. Lösche eine alte oder wechsle zu Plus für bis zu 10 Reisen.",
                    cancel: { label: "Später", flat: true },
                    ok: { label: "Upgrade", color: "primary", unelevated: true },
                }).onOk(() => void router.push("/account"));
                return;
            }
            $q.notify({ type: "negative", message: "Reise konnte nicht erstellt werden." });
        },
    });
}

function onDelete(journey: JourneyOut) {
    $q.dialog({
        title: "Reise löschen",
        message: `Möchtest du die Reise „${journey.name}“ wirklich löschen?`,
        cancel: { label: "Abbrechen", flat: true },
        ok: { label: "Löschen", color: "negative", unelevated: true },
        persistent: true,
    }).onOk(() => {
        deleteMutation.mutate(journey.id);
    });
}
</script>

<template>
    <q-page class="row justify-center q-pa-md">
        <div class="col-12 col-md-8 col-lg-6">
            <div class="row items-center q-mb-md">
                <q-btn flat round dense :icon="symSharpArrowBack" to="/" aria-label="Zurück" />
                <h1 class="text-h6 text-weight-bold q-my-none q-ml-sm col">Reisen</h1>
                <q-btn color="primary" unelevated no-caps :icon="symSharpAdd" label="Neue Reise" @click="showForm = true" />
            </div>

            <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>

            <div v-else-if="!journeys?.length" class="text-center text-muted q-mt-xl">
                <q-icon :name="symSharpLuggage" size="4rem" />
                <p class="q-mt-md text-body1">Noch keine Reise.</p>
                <p class="text-body2">
                    Gib Start, Ziel und wie weit du am Tag fahren willst an. NoRain schlägt Etappen, Pausen mit
                    Wasser und Toiletten, Übernachtungen und die beste Abfahrtszeit vor.
                </p>
            </div>

            <q-list v-else bordered separator class="rounded-borders">
                <q-item v-for="journey in journeys" :key="journey.id" clickable :to="`/journeys/${journey.id}`">
                    <q-item-section avatar>
                        <q-avatar rounded class="bg-tint-wet" text-color="primary" :icon="symSharpLuggage" />
                    </q-item-section>
                    <q-item-section>
                        <q-item-label class="text-weight-medium">{{ journey.name }}</q-item-label>
                        <q-item-label caption>{{ journey.startName }} → {{ journey.destName }}</q-item-label>
                        <q-item-label caption>
                            {{ journeyDates(journey) }}
                            <template v-if="journey.dayCount"> · {{ journey.dayCount }} Tag{{ journey.dayCount === 1 ? "" : "e" }}</template>
                        </q-item-label>
                    </q-item-section>
                    <q-item-section side>
                        <q-badge
                            v-if="journey.planStatus !== 'done'"
                            :color="journey.planStatus === 'failed' ? 'negative' : 'accent'"
                            :label="planStatusLabel(journey.planStatus)"
                        />
                    </q-item-section>
                    <q-item-section side>
                        <q-btn
                            flat
                            round
                            dense
                            :icon="symSharpDelete"
                            aria-label="Reise löschen"
                            @click.prevent.stop="onDelete(journey)"
                        />
                    </q-item-section>
                </q-item>
            </q-list>
        </div>
        <JourneyFormDialog v-model="showForm" @save="onSave" />
    </q-page>
</template>
