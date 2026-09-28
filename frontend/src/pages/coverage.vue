<route lang="json5">
{
    name: "coverage",
    meta: { titleKey: "pages.coverage" },
}
</route>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
    symSharpCheckCircle,
    symSharpConstruction,
    symSharpNotificationsActive,
    symSharpPublic,
    symSharpThumbUp,
} from "@quasar/extras/material-symbols-sharp";
import { type CoverageAreaOut, CoverageAreaOutStatusEnum as Status, SubscribeOutStatusEnum } from "@norain/api/models";

import { useSession } from "@/composables/useSession";
import { currentLocale, intlLocale } from "@/i18n";
import {
    confirmAreaSubscription,
    subscribeToArea,
    unsubscribeFromArea,
    useCoverage,
    useCoverageVote,
} from "@/queries/coverage";
import { apiErrorMessage } from "@/services/http";
import { areaName, areaNote, flagCode, rankedWishes, voteOptions, type AreaOption } from "@/utils/coverage";

// One lazy chunk per flag, loaded only for the areas shown. flag-icons' own stylesheet would
// inline every flag (~420 kB) into this public page.
const FLAG_DIR = "/node_modules/flag-icons/flags/4x3/";
const flagLoaders = import.meta.glob<string>("/node_modules/flag-icons/flags/4x3/*.svg", {
    query: "?url",
    import: "default",
});

const { t } = useI18n();
// Typed, and reactive: the catalogs' locale ref is read inside.
const locale = computed(() => currentLocale());
const $q = useQuasar();
const route = useRoute();
const router = useRouter();
const { session, sessionLoaded, refreshSession } = useSession();

const { data, isLoading, isError } = useCoverage();
const voteMutation = useCoverageVote();

const areas = computed(() => data.value?.areas ?? []);
const byCode = computed(() => new Map(areas.value.map(area => [area.code, area])));
const nameOf = (code: string) => areaName(code, byCode.value.get(code), locale.value);

const covered = computed(() =>
    areas.value
        .filter(area => area.status === Status.Covered)
        .sort((a, b) => nameOf(a.code).localeCompare(nameOf(b.code))),
);
const flagUrls = ref<Record<string, string>>({});
const flagUrl = (code: string) => {
    const flag = flagCode(code);
    return flag ? flagUrls.value[flag] : undefined;
};
watch(
    covered,
    list => {
        for (const area of list) {
            const flag = flagCode(area.code);
            const load = flag ? flagLoaders[`${FLAG_DIR}${flag}.svg`] : undefined;
            if (!flag || !load || flagUrls.value[flag]) continue;
            load().then(
                url => (flagUrls.value[flag] = url),
                () => undefined, // No flag: the check icon stays.
            );
        }
    },
    { immediate: true },
);
const planned = computed(() => areas.value.filter(area => area.status === Status.Planned));
const wishes = computed(() => rankedWishes(areas.value, locale.value).filter(area => area.status !== Status.Planned));
const options = computed(() => voteOptions(data.value?.countries ?? [], areas.value, locale.value));

const dateFormat = computed(() => new Intl.DateTimeFormat(intlLocale(locale.value), { dateStyle: "medium" }));
const since = (area: CoverageAreaOut) =>
    area.coveredSince ? t("coverage.since", { date: dateFormat.value.format(area.coveredSince) }) : null;

// --- The vote form ------------------------------------------------------------------------

const picked = ref<string | null>(null);
const filter = ref("");
const filtered = computed<AreaOption[]>(() => {
    const needle = filter.value.trim().toLocaleLowerCase(intlLocale(locale.value));
    return needle
        ? options.value.filter(
              option =>
                  option.label.toLocaleLowerCase(intlLocale(locale.value)).includes(needle) ||
                  option.code.toLowerCase() === needle,
          )
        : options.value;
});
function onFilter(value: string, update: (fn: () => void) => void) {
    update(() => {
        filter.value = value;
    });
}

const notify = ref(false);
const email = ref("");
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const emailValid = computed(() => EMAIL_PATTERN.test(email.value.trim()));
watch(
    () => session.value.user?.email,
    value => {
        if (value && !email.value) email.value = value;
    },
    { immediate: true },
);

const submitting = ref(false);
const pickedVoted = computed(() => (picked.value ? byCode.value.get(picked.value)?.voted === true : false));
const canSubmit = computed(() => !!picked.value && (!notify.value || emailValid.value) && !submitting.value);

/** POSTs need the CSRF cookie, which the session call sets, also for a visitor. */
async function ensureCsrf() {
    if (!sessionLoaded.value) await refreshSession();
}

async function submit() {
    const code = picked.value;
    if (!code || !canSubmit.value) return;
    submitting.value = true;
    try {
        await ensureCsrf();
        if (!pickedVoted.value) await voteMutation.mutateAsync({ code, voted: true });
        if (notify.value) {
            const reply = await subscribeToArea(code, email.value.trim());
            $q.notify({
                type: "positive",
                message:
                    reply.status === SubscribeOutStatusEnum.Confirmed
                        ? t("coverage.notifyConfirmed", { area: nameOf(code) })
                        : t("coverage.notifyPending", { email: email.value.trim() }),
            });
        } else {
            $q.notify({ type: "positive", message: t("coverage.voted", { area: nameOf(code) }) });
        }
        picked.value = null;
        notify.value = false;
    } catch (e) {
        $q.notify({ type: "negative", message: await apiErrorMessage(e, t("coverage.voteFailed")) });
    } finally {
        submitting.value = false;
    }
}

async function toggleVote(area: CoverageAreaOut) {
    try {
        await ensureCsrf();
        await voteMutation.mutateAsync({ code: area.code, voted: !area.voted });
    } catch (e) {
        $q.notify({ type: "negative", message: await apiErrorMessage(e, t("coverage.voteFailed")) });
    }
}

function askToNotify(area: CoverageAreaOut) {
    picked.value = area.code;
    notify.value = true;
    document.getElementById("coverage-vote")?.scrollIntoView({ behavior: "smooth", block: "center" });
}

// --- Links from the mails: ?confirm=… and ?unsubscribe=… -----------------------------------

const linkResult = ref<{ type: "positive" | "negative"; text: string } | null>(null);

onMounted(async () => {
    const confirm = typeof route.query.confirm === "string" ? route.query.confirm : null;
    const unsubscribe = typeof route.query.unsubscribe === "string" ? route.query.unsubscribe : null;
    if (!confirm && !unsubscribe) return;
    // The token is single-purpose; keep it out of the address bar and the history.
    void router.replace({ query: {} });
    try {
        await ensureCsrf();
        if (confirm) {
            const reply = await confirmAreaSubscription(confirm);
            linkResult.value = { type: "positive", text: t("coverage.confirmed", { area: nameOf(reply.code) }) };
        } else if (unsubscribe) {
            await unsubscribeFromArea(unsubscribe);
            linkResult.value = { type: "positive", text: t("coverage.unsubscribed") };
        }
    } catch (e) {
        linkResult.value = { type: "negative", text: await apiErrorMessage(e, t("coverage.linkFailed")) };
    }
});
</script>

<template>
    <q-page class="q-pa-md coverage-page">
        <h1 class="text-h5 text-weight-bold q-mt-none q-mb-xs">{{ t("coverage.title") }}</h1>
        <p class="text-body2 text-muted q-mb-md">{{ t("coverage.intro") }}</p>

        <q-banner
            v-if="linkResult"
            rounded
            :class="linkResult.type === 'positive' ? 'bg-tint-dry' : 'bg-tint-warn'"
            class="q-mb-md"
            role="status"
        >
            {{ linkResult.text }}
        </q-banner>

        <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>
        <q-banner v-else-if="isError" class="bg-tint-warn" rounded>{{ t("coverage.loadFailed") }}</q-banner>

        <template v-else>
            <section class="q-mb-lg" aria-labelledby="coverage-covered">
                <h2 id="coverage-covered" class="text-subtitle1 text-weight-bold q-my-sm">
                    {{ t("coverage.covered") }}
                </h2>
                <q-list bordered separator class="rounded-borders">
                    <q-item v-for="area in covered" :key="area.code">
                        <q-item-section avatar>
                            <!-- Decorative: the name beside it says the country. -->
                            <img v-if="flagUrl(area.code)" :src="flagUrl(area.code)" alt="" class="coverage-flag" />
                            <q-icon v-else :name="symSharpCheckCircle" color="positive" />
                        </q-item-section>
                        <q-item-section>
                            <q-item-label class="text-weight-medium">{{ nameOf(area.code) }}</q-item-label>
                            <q-item-label v-if="areaNote(area, locale) || since(area)" caption>
                                {{ [areaNote(area, locale), since(area)].filter(Boolean).join(" · ") }}
                            </q-item-label>
                        </q-item-section>
                    </q-item>
                    <q-item v-if="!covered.length">
                        <q-item-section class="text-muted">{{ t("coverage.noneCovered") }}</q-item-section>
                    </q-item>
                </q-list>
            </section>

            <section v-if="planned.length" class="q-mb-lg" aria-labelledby="coverage-planned">
                <h2 id="coverage-planned" class="text-subtitle1 text-weight-bold q-my-sm">
                    {{ t("coverage.planned") }}
                </h2>
                <q-list bordered separator class="rounded-borders">
                    <q-item v-for="area in planned" :key="area.code">
                        <q-item-section avatar><q-icon :name="symSharpConstruction" /></q-item-section>
                        <q-item-section>
                            <q-item-label class="text-weight-medium">{{ nameOf(area.code) }}</q-item-label>
                            <q-item-label v-if="areaNote(area, locale)" caption>
                                {{ areaNote(area, locale) }}
                            </q-item-label>
                        </q-item-section>
                        <q-item-section side>
                            <div class="row items-center no-wrap q-gutter-xs">
                                <q-btn
                                    flat
                                    round
                                    dense
                                    :icon="symSharpNotificationsActive"
                                    :aria-label="t('coverage.notifyFor', { area: nameOf(area.code) })"
                                    @click="askToNotify(area)"
                                >
                                    <q-tooltip>{{ t("coverage.notifyFor", { area: nameOf(area.code) }) }}</q-tooltip>
                                </q-btn>
                                <q-btn
                                    :outline="!area.voted"
                                    :unelevated="area.voted"
                                    color="primary"
                                    dense
                                    no-caps
                                    :icon="symSharpThumbUp"
                                    :label="String(area.votes ?? 0)"
                                    :aria-pressed="area.voted === true"
                                    :aria-label="
                                        t(area.voted ? 'coverage.withdraw' : 'coverage.voteFor', {
                                            area: nameOf(area.code),
                                        })
                                    "
                                    @click="toggleVote(area)"
                                />
                            </div>
                        </q-item-section>
                    </q-item>
                </q-list>
            </section>

            <section id="coverage-vote" class="q-mb-lg" aria-labelledby="coverage-wish">
                <h2 id="coverage-wish" class="text-subtitle1 text-weight-bold q-my-sm">{{ t("coverage.wish") }}</h2>
                <q-card flat bordered>
                    <q-card-section class="q-gutter-y-sm">
                        <div class="text-body2 text-muted">{{ t("coverage.wishHint") }}</div>
                        <q-select
                            v-model="picked"
                            :options="filtered"
                            option-value="code"
                            option-label="label"
                            emit-value
                            map-options
                            use-input
                            input-debounce="0"
                            outlined
                            dense
                            clearable
                            :label="t('coverage.pick')"
                            @filter="onFilter"
                        >
                            <template #prepend><q-icon :name="symSharpPublic" /></template>
                            <template #no-option>
                                <q-item>
                                    <q-item-section class="text-muted">{{ t("coverage.noMatch") }}</q-item-section>
                                </q-item>
                            </template>
                        </q-select>
                        <q-checkbox v-model="notify" :label="t('coverage.notifyMe')" />
                        <q-input
                            v-if="notify"
                            v-model="email"
                            type="email"
                            outlined
                            dense
                            autocomplete="email"
                            :label="t('coverage.email')"
                            :hint="t('coverage.emailHint')"
                            :error="email.length > 0 && !emailValid"
                            :error-message="t('coverage.emailInvalid')"
                        />
                        <div class="row items-center q-gutter-sm">
                            <q-btn
                                color="primary"
                                unelevated
                                no-caps
                                :loading="submitting"
                                :disable="!canSubmit"
                                :label="pickedVoted && notify ? t('coverage.notifySubmit') : t('coverage.submit')"
                                @click="submit"
                            />
                            <span v-if="pickedVoted" class="text-caption text-muted">
                                {{ t("coverage.alreadyVoted") }}
                            </span>
                        </div>
                    </q-card-section>
                </q-card>
            </section>

            <section aria-labelledby="coverage-ranking">
                <h2 id="coverage-ranking" class="text-subtitle1 text-weight-bold q-my-sm">
                    {{ t("coverage.ranking") }}
                </h2>
                <div v-if="!wishes.length" class="text-body2 text-muted">{{ t("coverage.noVotes") }}</div>
                <q-list v-else bordered separator class="rounded-borders">
                    <q-item v-for="(area, index) in wishes" :key="area.code">
                        <q-item-section avatar class="text-muted text-weight-bold">{{ index + 1 }}.</q-item-section>
                        <q-item-section>
                            <q-item-label class="text-weight-medium">{{ nameOf(area.code) }}</q-item-label>
                            <q-item-label caption>{{ t("coverage.votes", area.votes ?? 0) }}</q-item-label>
                        </q-item-section>
                        <q-item-section side>
                            <div class="row items-center no-wrap q-gutter-xs">
                                <q-btn
                                    flat
                                    round
                                    dense
                                    :icon="symSharpNotificationsActive"
                                    :aria-label="t('coverage.notifyFor', { area: nameOf(area.code) })"
                                    @click="askToNotify(area)"
                                >
                                    <q-tooltip>{{ t("coverage.notifyFor", { area: nameOf(area.code) }) }}</q-tooltip>
                                </q-btn>
                                <q-btn
                                    :outline="!area.voted"
                                    :unelevated="area.voted"
                                    color="primary"
                                    dense
                                    no-caps
                                    :icon="symSharpThumbUp"
                                    :label="area.voted ? t('coverage.votedShort') : t('coverage.voteShort')"
                                    :aria-pressed="area.voted === true"
                                    :aria-label="
                                        t(area.voted ? 'coverage.withdraw' : 'coverage.voteFor', {
                                            area: nameOf(area.code),
                                        })
                                    "
                                    @click="toggleVote(area)"
                                />
                            </div>
                        </q-item-section>
                    </q-item>
                </q-list>
            </section>
        </template>
    </q-page>
</template>

<style scoped>
.coverage-page {
    max-width: 720px;
    margin: 0 auto;
}

/* The outline keeps white-edged flags (JP, PL…) visible in both themes. */
.coverage-flag {
    width: 1.6em;
    height: 1.2em;
    object-fit: cover;
    border-radius: 2px;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.12);
}
</style>
