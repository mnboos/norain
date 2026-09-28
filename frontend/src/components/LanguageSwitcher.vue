<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useQuasar } from "quasar";

import { useLocale } from "@/composables/useLocale";
import type { AppLocale } from "@/i18n";

defineProps<{ dense?: boolean }>();

const { t } = useI18n();
const $q = useQuasar();
const { locale, setLocale } = useLocale();
const saving = ref(false);

const options = [
    { label: "DE", value: "de" },
    { label: "EN", value: "en" },
] satisfies { label: string; value: AppLocale }[];

async function choose(value: AppLocale) {
    if (value === locale.value) return;
    saving.value = true;
    try {
        await setLocale(value);
    } catch {
        // The app already switched; only saving it on the account failed.
        $q.notify({ type: "warning", message: t("language.saveFailed") });
    } finally {
        saving.value = false;
    }
}
</script>

<template>
    <q-btn-toggle
        :model-value="locale"
        :options="options"
        :disable="saving"
        :dense="dense"
        no-caps
        unelevated
        size="sm"
        toggle-color="primary"
        :aria-label="t('language.label')"
        @update:model-value="choose"
    />
</template>
