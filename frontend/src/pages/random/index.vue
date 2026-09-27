<route lang="json5">
{
    name: "random-rides",
    meta: { title: "Zufallsrunden", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { ref } from "vue";
import { useQuasar } from "quasar";
import { useRouter } from "vue-router";
import { symSharpAdd, symSharpArrowBack, symSharpCasino, symSharpDelete } from "@quasar/extras/material-symbols-sharp";
import type { JourneyIn, JourneyOut } from "@norain/api/models";
import RandomRideFormDialog from "@/components/random/RandomRideFormDialog.vue";
import { isQuotaExceeded } from "@/services/http";
import { JourneyKind, useCreateJourney, useDeleteJourney, useJourneys } from "@/queries/journeys";
import { dayLabel, duration, km, planStatusLabel } from "@/utils/journeys";

const $q = useQuasar();
const router = useRouter();
const showForm = ref(false);

const { data: rides, isLoading } = useJourneys(JourneyKind.Random);
const createMutation = useCreateJourney();
const deleteMutation = useDeleteJourney();

function onSave(data: JourneyIn) {
    createMutation.mutate(data, {
        onSuccess: ride => void router.push(`/journeys/${ride.id}`),
        onError: (err: unknown) => {
            if (isQuotaExceeded(err)) {
                $q.dialog({
                    title: "Tarifgrenze erreicht",
                    message:
                        "Dein Tarif erlaubt drei Zufallsrunden. Lösche eine alte oder wechsle zu Plus für bis zu 20.",
                    cancel: { label: "Später", flat: true },
                    ok: { label: "Upgrade", color: "primary", unelevated: true },
                }).onOk(() => void router.push("/account"));
                return;
            }
            $q.notify({ type: "negative", message: "Runde konnte nicht erstellt werden." });
        },
    });
}

function onDelete(ride: JourneyOut) {
    $q.dialog({
        title: "Runde löschen",
        message: `Möchtest du die Runde „${ride.name}“ wirklich löschen?`,
        cancel: { label: "Abbrechen", flat: true },
        ok: { label: "Löschen", color: "negative", unelevated: true },
        persistent: true,
    }).onOk(() => {
        deleteMutation.mutate(ride.id);
    });
}

function target(ride: JourneyOut): string {
    const length = ride.maxDaySeconds ? duration(ride.maxDaySeconds) : km(ride.maxDayDistanceM ?? 0);
    return (ride.randomPrefs?.roundTrip ?? true)
        ? `Rundkurs ab ${ride.startName} · ${length}`
        : `${ride.startName} → ${ride.destName} · ${length}`;
}
</script>

<template>
    <q-page class="row justify-center q-pa-md">
        <div class="col-12 col-md-8 col-lg-6">
            <div class="row items-center q-mb-md">
                <q-btn flat round dense :icon="symSharpArrowBack" to="/" aria-label="Zurück" />
                <h1 class="text-h6 text-weight-bold q-my-none q-ml-sm col">Zufallsrunden</h1>
                <q-btn
                    color="primary"
                    unelevated
                    no-caps
                    :icon="symSharpAdd"
                    label="Neue Runde"
                    @click="showForm = true"
                />
            </div>

            <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>

            <div v-else-if="!rides?.length" class="text-center text-muted q-mt-xl">
                <q-icon :name="symSharpCasino" size="4rem" />
                <p class="q-mt-md text-body1">Noch keine Zufallsrunde.</p>
                <p class="text-body2">
                    Keine Idee, wohin? Sag, ob du im Kreis oder zu einem Ziel fahren willst und wie lange oder wie weit.
                    Meteolane würfelt die Strecke und zeigt das Wetter darauf.
                </p>
            </div>

            <q-list v-else bordered separator class="rounded-borders">
                <q-item v-for="ride in rides" :key="ride.id" clickable :to="`/journeys/${ride.id}`">
                    <q-item-section avatar>
                        <q-avatar rounded class="bg-tint-wet" text-color="primary" :icon="symSharpCasino" />
                    </q-item-section>
                    <q-item-section>
                        <q-item-label class="text-weight-medium">{{ ride.name }}</q-item-label>
                        <q-item-label caption>{{ target(ride) }}</q-item-label>
                        <q-item-label caption>
                            {{ dayLabel(ride.startDate) }} · {{ ride.earliestStart.slice(0, 5) }}
                        </q-item-label>
                    </q-item-section>
                    <q-item-section side>
                        <q-badge
                            v-if="ride.planStatus !== 'done'"
                            :color="ride.planStatus === 'failed' ? 'negative' : 'accent'"
                            :label="planStatusLabel(ride.planStatus)"
                        />
                    </q-item-section>
                    <q-item-section side>
                        <q-btn
                            flat
                            round
                            dense
                            :icon="symSharpDelete"
                            aria-label="Runde löschen"
                            @click.prevent.stop="onDelete(ride)"
                        />
                    </q-item-section>
                </q-item>
            </q-list>
        </div>
        <RandomRideFormDialog v-model="showForm" @save="onSave" />
    </q-page>
</template>
