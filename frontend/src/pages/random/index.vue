<route lang="json5">
{
    name: "random-rides",
    meta: { titleKey: "pages.randomRides", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { useEntitlements } from "@/composables/useEntitlements";
import { ref } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { symSharpAdd, symSharpArrowBack, symSharpCasino, symSharpDelete } from "@quasar/extras/material-symbols-sharp";
import type { JourneyIn, JourneyOut } from "@norain/api/models";
import RandomRideFormDialog from "@/components/random/RandomRideFormDialog.vue";
import { isQuotaExceeded } from "@/services/http";
import { JourneyKind, useCreateJourney, useDeleteJourney, useJourneys } from "@/queries/journeys";
import { dayLabel, duration, km, planStatusLabel } from "@/utils/journeys";

const $q = useQuasar();
const { t } = useI18n();
const router = useRouter();
const showForm = ref(false);

const { data: rides, isLoading } = useJourneys(JourneyKind.Random);
const { entitlements } = useEntitlements();
const createMutation = useCreateJourney();
const deleteMutation = useDeleteJourney();

function onSave(data: JourneyIn) {
    createMutation.mutate(data, {
        onSuccess: ride => void router.push(`/journeys/${ride.id}`),
        onError: (err: unknown) => {
            if (isQuotaExceeded(err)) {
                $q.dialog({
                    title: t("quota.title"),
                    message: entitlements.value?.offer
                        ? t(
                              "quota.randomRides",
                              { n: entitlements.value.offer.freeRandomRides, plus: entitlements.value.offer.plusRandomRides },
                              entitlements.value.offer.freeRandomRides,
                          )
                        : undefined,
                    cancel: { label: t("quota.later"), flat: true },
                    ok: { label: t("quota.upgrade"), color: "primary", unelevated: true },
                }).onOk(() => void router.push("/account"));
                return;
            }
            $q.notify({ type: "negative", message: t("random.createFailed") });
        },
    });
}

function onDelete(ride: JourneyOut) {
    $q.dialog({
        title: t("random.delete.title"),
        message: t("random.delete.message", { name: ride.name }),
        cancel: { label: t("common.cancel"), flat: true },
        ok: { label: t("common.delete"), color: "negative", unelevated: true },
        persistent: true,
    }).onOk(() => {
        deleteMutation.mutate(ride.id);
    });
}

function target(ride: JourneyOut): string {
    const length = ride.maxDaySeconds ? duration(ride.maxDaySeconds) : km(ride.maxDayDistanceM ?? 0);
    return (ride.randomPrefs?.roundTrip ?? true)
        ? t("random.loopFrom", { start: ride.startName, length })
        : `${ride.startName} → ${ride.destName} · ${length}`;
}
</script>

<template>
    <q-page class="row justify-center q-pa-md">
        <div class="col-12 col-md-8 col-lg-6">
            <div class="row items-center q-mb-md">
                <q-btn flat round dense :icon="symSharpArrowBack" to="/routes" :aria-label="t('common.back')" />
                <h1 class="text-h6 text-weight-bold q-my-none q-ml-sm col">{{ t("pages.randomRides") }}</h1>
                <q-btn
                    color="primary"
                    unelevated
                    no-caps
                    :icon="symSharpAdd"
                    :label="t('random.new')"
                    @click="showForm = true"
                />
            </div>

            <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>

            <div v-else-if="!rides?.length" class="text-center text-muted q-mt-xl">
                <q-icon :name="symSharpCasino" size="4rem" />
                <p class="q-mt-md text-body1">{{ t("random.empty") }}</p>
                <p class="text-body2">{{ t("random.emptyHint") }}</p>
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
                            :aria-label="t('random.delete.title')"
                            @click.prevent.stop="onDelete(ride)"
                        />
                    </q-item-section>
                </q-item>
            </q-list>
        </div>
        <RandomRideFormDialog v-model="showForm" @save="onSave" />
    </q-page>
</template>
