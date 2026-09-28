<script setup lang="ts">
import { computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useEntitlements } from "@/composables/useEntitlements";
const { t } = useI18n();
const { isPro } = useEntitlements();
const before = defineModel<number>("before", { default: 0 });
const after = defineModel<number>("after", { default: 0 });
watch(isPro, value => { if (!value) { before.value = 0; after.value = 0; } }, { immediate: true });
const options = computed(() =>
    Array.from({ length: 9 }, (_, i) => ({
        label: i === 0 ? t("flexibility.none") : `${i * 15} min`,
        value: i * 15,
    })),
);
</script>

<template>
    <q-expansion-item
        :label="t('flexibility.advanced')"
        :caption="before || after ? t('flexibility.caption', { before, after }) : undefined"
        dense
    >
        <div v-if="!isPro" class="q-pa-sm">
            <p>{{ t("flexibility.plusPitch") }}</p>
            <q-btn to="/account" color="primary" :label="t('plan.tryPlus')" no-caps flat />
        </div>
        <div v-else class="q-pa-sm">
            <div class="text-h6">{{ t("flexibility.title") }}</div>
            <p class="text-body2">{{ t("flexibility.intro") }}</p>
            <div class="row q-col-gutter-sm">
                <q-select
                    v-model="before"
                    class="col"
                    :label="t('flexibility.earlier')"
                    :options="options"
                    emit-value
                    map-options
                    outlined
                    dense
                />
                <q-select
                    v-model="after"
                    class="col"
                    :label="t('flexibility.later')"
                    :options="options"
                    emit-value
                    map-options
                    outlined
                    dense
                />
            </div>
            <slot />
        </div>
    </q-expansion-item>
</template>
