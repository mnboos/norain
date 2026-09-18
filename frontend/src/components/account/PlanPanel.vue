<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useEntitlements } from "@/composables/useEntitlements";
import { billingApi } from "@/services/billing";
import { briefingsApi, enablePush, disablePush, pushSupported } from "@/services/briefings";

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
const date = (value: string) => new Date(value).toLocaleDateString("de-DE");
async function run(action: () => Promise<unknown>, success = "") {
    busy.value = true;
    error.value = "";
    message.value = "";
    try {
        await action();
        await client.invalidateQueries();
        message.value = success;
    } catch (e) {
        error.value = e instanceof Error ? e.message : "Bitte versuche es erneut.";
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
        { label: "Aus", value: "" },
        { label: "E-Mail", value: "email", disable: !isPro.value || !preferences.data.value?.emailConfigured },
        {
            label: "Push",
            value: "push",
            disable: !isPro.value || !preferences.data.value?.pushPublicKey || !preferences.data.value.pushDeviceCount,
        },
    ].map(option => ({ ...option, disable: option.value === current ? false : option.disable }));
}
</script>

<template>
    <section aria-label="NoRain Tarife">
        <h2 class="text-h6">{{ isPro ? "NoRain Plus" : "NoRain Free" }}</h2>
        <p>Finde eine bessere Abfahrtszeit und erhalte deine Vorhersage vor der Fahrt.</p>
        <q-banner v-if="error" role="alert" class="bg-tint-error q-mb-md">{{ error }}</q-banner>
        <q-banner v-if="message" role="status" class="q-mb-md">{{ message }}</q-banner>
        <p v-if="entitlements">{{ entitlements.routeCount }} / {{ entitlements.maxRoutes }} aktive Routen</p>
        <p v-if="entitlements?.complimentaryUntil && new Date(entitlements.complimentaryUntil) > new Date()">
            Plus geschenkt bis {{ date(entitlements.complimentaryUntil) }}. Keine automatische Zahlung.
        </p>
        <p v-else-if="isPro && entitlements?.trialEndsAt && !entitlements.paidSubscription">
            Testphase bis {{ date(entitlements.trialEndsAt) }}. Danach automatisch Free, ohne Zahlung.
        </p>
        <p v-if="entitlements?.cancelAtPeriodEnd && entitlements.currentPeriodEnd">
            Dein Abo endet am {{ date(entitlements.currentPeriodEnd) }}.
        </p>
        <ul class="q-pl-md">
            <li>Hin- und Rückfahrt zählen zusammen als eine Route.</li>
            <li>Free: 2 Routen, Wetterkarten, Regenrisiko, Temperatur und Wind.</li>
            <li>Plus: 20 Routen, automatischer Abfahrtsvergleich und Wetterdetails.</li>
            <li>Plus: Briefings für bis zu 5 Routen, per E-Mail oder Push.</li>
        </ul>
        <p>
            <strong>29 € pro Jahr</strong>
            oder
            <strong>3,90 € pro Monat</strong>
            .
        </p>
        <div class="q-gutter-sm">
            <q-btn
                v-if="entitlements?.trialEligible"
                color="primary"
                no-caps
                label="14 Tage kostenlos testen"
                :loading="busy"
                @click="run(billingApi.trial, 'Plus ist jetzt aktiv. Viel Freude bei deinen Fahrten!')"
            />
            <template v-if="entitlements?.billingConfigured && !entitlements.paidSubscription">
                <q-btn
                    color="primary"
                    no-caps
                    label="29 € / Jahr"
                    :loading="busy"
                    @click="run(() => checkout('annual'))"
                />
                <q-btn outline no-caps label="3,90 € / Monat" :loading="busy" @click="run(() => checkout('monthly'))" />
            </template>
            <q-btn
                v-if="entitlements?.paidSubscription || entitlements?.status === 'past_due'"
                outline
                no-caps
                label="Abo verwalten"
                :loading="busy"
                @click="run(portal)"
            />
        </div>
        <p v-if="entitlements?.trialEligible" class="text-caption q-mt-sm">
            Ohne Kreditkarte. Keine automatische Verlängerung der Testphase.
        </p>
        <p v-if="!entitlements?.billingConfigured" class="text-caption">
            Beta: Der Kauf von Plus ist noch nicht freigeschaltet.
        </p>

        <template v-if="routes.length">
            <h3 class="text-subtitle1 q-mt-lg">Deine zwei Free-Routen</h3>
            <p class="text-caption">
                Diese Routen bleiben nach Plus aktiv. Weitere Routen bleiben gespeichert und pausieren.
            </p>
            <q-select
                v-model="selected"
                multiple
                emit-value
                map-options
                :max-values="2"
                outlined
                label="Bis zu zwei Routen auswählen"
                :options="routes.filter(r => r.active).map(r => ({ label: r.name, value: r.id }))"
            />
            <q-btn
                flat
                no-caps
                label="Auswahl speichern"
                :loading="busy"
                @click="run(() => billingApi.selectFreeRoutes(selected), 'Auswahl gespeichert.')"
            />
        </template>

        <h3 class="text-subtitle1 q-mt-lg">Vor der Fahrt informiert</h3>
        <p class="text-caption">
            Ein Briefing je Hinfahrt und Rückfahrt etwa 60 Minuten vor der frühesten Abfahrt in deinem Zeitfenster. Bis
            zu 10 Briefings pro Tag. Keine laufenden Wetterwarnungen.
        </p>
        <p v-if="!isPro">Aktiviere Plus, um Briefings einzurichten.</p>
        <p v-if="!pushSupported()" class="text-caption">
            Dieser Browser unterstützt Push hier nicht. Nutze E-Mail. Auf dem iPhone installierst du NoRain zuerst über
            „Zum Home-Bildschirm“.
        </p>
        <div class="q-gutter-sm q-mb-md">
            <q-btn
                v-if="isPro && preferences.data.value?.pushPublicKey && pushSupported()"
                outline
                no-caps
                label="Push auf diesem Gerät aktivieren"
                :loading="busy"
                @click="
                    run(
                        () => enablePush(preferences.data.value!.pushPublicKey),
                        'Gerät verbunden. Wähle jetzt Push für deine Route.',
                    )
                "
            />
            <q-btn
                v-if="pushSupported()"
                flat
                no-caps
                label="Push auf diesem Gerät ausschalten"
                :loading="busy"
                @click="run(disablePush, 'Push auf diesem Gerät ausgeschaltet.')"
            />
        </div>
        <p v-if="preferences.isError.value" role="alert">Briefing-Einstellungen konnten nicht geladen werden.</p>
        <p
            v-else-if="
                isPro &&
                preferences.data.value &&
                !preferences.data.value.emailConfigured &&
                !preferences.data.value.pushPublicKey
            "
            class="text-caption"
        >
            Der Versand ist noch nicht eingerichtet. Deine Vorhersagen sind weiterhin in der App verfügbar.
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
                    (value: string) =>
                        run(() => briefingsApi.update(route.id, value), 'Briefing-Einstellung gespeichert.')
                "
            />
            <span v-if="!route.available || (route.channel && !route.briefingActive)" class="text-caption">
                Pausiert durch deinen Tarif.
            </span>
        </div>
        <q-expansion-item v-if="preferences.data.value?.recent.length" label="Letzte Briefings">
            <article v-for="briefing in preferences.data.value.recent" :key="briefing.id" class="q-pa-sm">
                <strong>{{ briefing.routeName }} · {{ date(briefing.departure) }}</strong>
                <p style="white-space: pre-line">{{ briefing.body }}</p>
                <p v-if="briefing.status !== 'sent'" class="text-caption">
                    Zustellung nicht bestätigt. Hier in der App verfügbar.
                </p>
            </article>
        </q-expansion-item>
    </section>
</template>
