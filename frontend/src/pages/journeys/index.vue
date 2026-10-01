<route lang="json5">
{
    name: "journeys",
    meta: { titleKey: "pages.journeys", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { useEntitlements } from "@/composables/useEntitlements";
import { ref } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { symSharpAdd, symSharpArrowBack, symSharpDelete, symSharpLuggage } from "@quasar/extras/material-symbols-sharp";
import type { JourneyIn, JourneyOut } from "@norain/api/models";
import JourneyFormDialog from "@/components/journey/JourneyFormDialog.vue";
import { isQuotaExceeded } from "@/services/http";
import { JourneyKind, useCreateJourney, useDeleteJourney, useJourneys } from "@/queries/journeys";
import { journeyDates, planStatusLabel } from "@/utils/journeys";

const $q = useQuasar();
const { t } = useI18n();
const router = useRouter();
const showForm = ref(false);

const { data: journeys, isLoading } = useJourneys(JourneyKind.Tour);
const { entitlements } = useEntitlements();
const createMutation = useCreateJourney();
const deleteMutation = useDeleteJourney();

function onSave(data: JourneyIn) {
    createMutation.mutate(data, {
        onSuccess: journey => void router.push(`/journeys/${journey.id}`),
        onError: (err: unknown) => {
            if (isQuotaExceeded(err)) {
                $q.dialog({
                    title: t("quota.title"),
                    message: entitlements.value?.offer
                        ? t(
                              "quota.journeys",
                              { n: entitlements.value.offer.freeJourneys, plus: entitlements.value.offer.plusJourneys },
                              entitlements.value.offer.freeJourneys,
                          )
                        : undefined,
                    cancel: { label: t("quota.later"), flat: true },
                    ok: { label: t("quota.upgrade"), color: "primary", unelevated: true },
                }).onOk(() => void router.push("/account"));
                return;
            }
            $q.notify({ type: "negative", message: t("journeys.createFailed") });
        },
    });
}

function onDelete(journey: JourneyOut) {
    $q.dialog({
        title: t("journeys.delete.title"),
        message: t("journeys.delete.message", { name: journey.name }),
        cancel: { label: t("common.cancel"), flat: true },
        ok: { label: t("common.delete"), color: "negative", unelevated: true },
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
                <q-btn flat round dense :icon="symSharpArrowBack" to="/routes" :aria-label="t('common.back')" />
                <h1 class="text-h6 text-weight-bold q-my-none q-ml-sm col">{{ t("pages.journeys") }}</h1>
                <q-btn color="primary" unelevated no-caps :icon="symSharpAdd" :label="t('journeys.new')" @click="showForm = true" />
            </div>

            <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>

            <div v-else-if="!journeys?.length" class="text-center text-muted q-mt-xl">
                <q-icon :name="symSharpLuggage" size="4rem" />
                <p class="q-mt-md text-body1">{{ t("journeys.empty") }}</p>
                <p class="text-body2">{{ t("journeys.emptyHint") }}</p>
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
                            <template v-if="journey.dayCount"> · {{ t("journeys.days", journey.dayCount) }}</template>
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
                            :aria-label="t('journeys.delete.title')"
                            @click.prevent.stop="onDelete(journey)"
                        />
                    </q-item-section>
                </q-item>
            </q-list>
        </div>
        <JourneyFormDialog v-model="showForm" @save="onSave" />
    </q-page>
</template>
