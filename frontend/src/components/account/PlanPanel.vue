<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { intlLocale } from "@/i18n";
import { useEntitlements } from "@/composables/useEntitlements";
import { billingApi } from "@/services/billing";
import { briefingsApi, enablePush, disablePush, pushSupported } from "@/services/briefings";

const { t } = useI18n();
const client = useQueryClient();
const { entitlements, isPro } = useEntitlements();
const busy = ref(false);
const error = ref("");
const message = ref("");
const selected = ref<string[]>([]);
const preferences = useQuery({
    queryKey: ["briefingPreferences"],
    queryFn: briefingsApi.preferences,
    refetchInterval: 60_000,
});
const routes = computed(() => preferences.data.value?.routes ?? []);
watch(
    () => preferences.data.value,
    value => {
        if (value) {
            const explicit = value.routes.filter(r => r.freeSelected && r.active);
            selected.value = (explicit.length ? explicit : value.routes.filter(r => r.active))
                .slice(0, 2)
                .map(r => r.id);
        }
    },
    { immediate: true },
);
const date = (value: string) => new Date(value).toLocaleDateString(intlLocale());
async function run(action: () => Promise<unknown>, success = "") {
    busy.value = true;
    error.value = "";
    message.value = "";
    try {
        await action();
        await client.invalidateQueries();
        message.value = success;
    } catch (e) {
        error.value = e instanceof Error ? e.message : t("plan.tryAgain");
    } finally {
        busy.value = false;
    }
}
async function portal() {
    window.location.href = (await billingApi.portal()).url;
}
async function checkout(interval: "annual" | "monthly") {
    window.location.href = (await billingApi.checkout(interval)).url;
}
function channels(current: string) {
    return [
        { label: t("share.zoneOff"), value: "" },
        { label: t("plan.email"), value: "email", disable: !isPro.value || !preferences.data.value?.emailConfigured },
        {
            label: "Push",
            value: "push",
            disable: !isPro.value || !preferences.data.value?.pushPublicKey || !preferences.data.value.pushDeviceCount,
        },
    ].map(option => ({ ...option, disable: option.value === current ? false : option.disable }));
}
</script>

<template>
    <section :aria-label="t('plan.label')">
        <h2 class="text-h6">{{ isPro ? "MeteoLane Plus" : "MeteoLane Free" }}</h2>
        <p>{{ t("plan.pitch") }}</p>
        <q-banner v-if="error" role="alert" class="bg-tint-error q-mb-md">{{ error }}</q-banner>
        <q-banner v-if="message" role="status" class="q-mb-md">{{ message }}</q-banner>
        <p v-if="entitlements">
            {{ t("plan.activeRoutes", { count: entitlements.routeCount, max: entitlements.maxRoutes }) }}
        </p>
        <p v-if="entitlements?.complimentaryUntil && new Date(entitlements.complimentaryUntil) > new Date()">
            {{ t("plan.complimentary", { date: date(entitlements.complimentaryUntil) }) }}
        </p>
        <p v-else-if="isPro && entitlements?.trialEndsAt && !entitlements.paidSubscription">
            {{ t("plan.trialUntil", { date: date(entitlements.trialEndsAt) }) }}
        </p>
        <p v-if="entitlements?.cancelAtPeriodEnd && entitlements.currentPeriodEnd">
            {{ t("plan.endsOn", { date: date(entitlements.currentPeriodEnd) }) }}
        </p>
        <ul class="q-pl-md">
            <li>{{ t("plan.feature.twoWay") }}</li>
            <li>{{ t("plan.feature.free") }}</li>
            <li>{{ t("plan.feature.plus") }}</li>
            <li>{{ t("plan.feature.briefings") }}</li>
        </ul>
        <p>
            <strong>{{ t("plan.perYear") }}</strong>
            {{ t("plan.or") }}
            <strong>{{ t("plan.perMonth") }}</strong>
            .
        </p>
        <div class="q-gutter-sm">
            <q-btn
                v-if="entitlements?.trialEligible"
                color="primary"
                no-caps
                :label="t('plan.startTrial')"
                :loading="busy"
                @click="run(billingApi.trial, t('plan.trialStarted'))"
            />
            <template v-if="entitlements?.billingConfigured && !entitlements.paidSubscription">
                <q-btn
                    color="primary"
                    no-caps
                    :label="t('plan.yearly')"
                    :loading="busy"
                    @click="run(() => checkout('annual'))"
                />
                <q-btn
                    outline
                    no-caps
                    :label="t('plan.monthly')"
                    :loading="busy"
                    @click="run(() => checkout('monthly'))"
                />
            </template>
            <q-btn
                v-if="entitlements?.paidSubscription || entitlements?.status === 'past_due'"
                outline
                no-caps
                :label="t('plan.manage')"
                :loading="busy"
                @click="run(portal)"
            />
        </div>
        <p v-if="entitlements?.trialEligible" class="text-caption q-mt-sm">
            {{ t("plan.noCard") }}
        </p>
        <p v-if="!entitlements?.billingConfigured" class="text-caption">
            {{ t("plan.beta") }}
        </p>

        <template v-if="routes.length">
            <h3 class="text-subtitle1 q-mt-lg">{{ t("plan.freeRoutes") }}</h3>
            <p class="text-caption">{{ t("plan.freeRoutesHint") }}</p>
            <q-select
                v-model="selected"
                multiple
                emit-value
                map-options
                :max-values="2"
                outlined
                :label="t('plan.pickFreeRoutes')"
                :options="routes.filter(r => r.active).map(r => ({ label: r.name, value: r.id }))"
            />
            <q-btn
                flat
                no-caps
                :label="t('plan.saveSelection')"
                :loading="busy"
                @click="run(() => billingApi.selectFreeRoutes(selected), t('plan.selectionSaved'))"
            />
        </template>

        <h3 class="text-subtitle1 q-mt-lg">{{ t("plan.briefingsTitle") }}</h3>
        <p class="text-caption">{{ t("plan.briefingsHint") }}</p>
        <p v-if="!isPro">{{ t("plan.briefingsNeedPlus") }}</p>
        <p v-if="!pushSupported()" class="text-caption">{{ t("plan.noPush") }}</p>
        <div class="q-gutter-sm q-mb-md">
            <q-btn
                v-if="isPro && preferences.data.value?.pushPublicKey && pushSupported()"
                outline
                no-caps
                :label="t('plan.pushOn')"
                :loading="busy"
                @click="run(() => enablePush(preferences.data.value!.pushPublicKey), t('plan.pushOnDone'))"
            />
            <q-btn
                v-if="pushSupported()"
                flat
                no-caps
                :label="t('plan.pushOff')"
                :loading="busy"
                @click="run(disablePush, t('plan.pushOffDone'))"
            />
        </div>
        <p v-if="preferences.isError.value" role="alert">{{ t("plan.briefingsLoadFailed") }}</p>
        <p
            v-else-if="
                isPro &&
                preferences.data.value &&
                !preferences.data.value.emailConfigured &&
                !preferences.data.value.pushPublicKey
            "
            class="text-caption"
        >
            {{ t("plan.deliveryMissing") }}
        </p>
        <div v-for="route in routes" :key="route.id" class="q-mb-md">
            <q-select
                :model-value="route.channel"
                :label="route.name"
                outlined
                dense
                emit-value
                map-options
                :options="channels(route.channel)"
                :disable="busy || !route.active"
                @update:model-value="
                    (value: string) => run(() => briefingsApi.update(route.id, value), t('plan.briefingSaved'))
                "
            />
            <span v-if="!route.available || (route.channel && !route.briefingActive)" class="text-caption">
                {{ t("plan.paused") }}
            </span>
        </div>
        <q-expansion-item v-if="preferences.data.value?.recent.length" :label="t('plan.recentBriefings')">
            <article v-for="briefing in preferences.data.value.recent" :key="briefing.id" class="q-pa-sm">
                <strong>{{ briefing.routeName }} · {{ date(briefing.departure) }}</strong>
                <p style="white-space: pre-line">{{ briefing.body }}</p>
                <p v-if="briefing.status !== 'sent'" class="text-caption">
                    {{ t("plan.notDelivered") }}
                </p>
            </article>
        </q-expansion-item>
    </section>
</template>
