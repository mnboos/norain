<script setup lang="ts">
import { computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useEntitlements } from "@/composables/useEntitlements";
const { t } = useI18n();
const { isPro } = useEntitlements();
const before = defineModel<number>("before", { default: 0 });
const after = defineModel<number>("after", { default: 0 });
watch(
    isPro,
    value => {
        if (!value) {
            before.value = 0;
            after.value = 0;
        }
    },
    { immediate: true },
);
const options = computed(() =>
    Array.from({ length: 9 }, (_, i) => ({
        label: i === 0 ? t("flexibility.none") : `${i * 15} min`,
        value: i * 15,
    })),
);
</script>

<template>
    <q-card bordered flat>
        <q-expansion-item
            :label="t('flexibility.advanced')"
            :caption="before || after ? t('flexibility.caption', { before, after }) : undefined"
            dense
        >
            <q-card-section v-if="!isPro" class="">
                <p>{{ t("flexibility.plusPitch") }}</p>
                <q-btn to="/account" color="primary" :label="t('plan.tryPlus')" no-caps flat />
            </q-card-section>
            <q-card-section v-else class="">
                <q-item-label overline>{{ t("flexibility.title") }}</q-item-label>
                <!--                <q-item-label>{{ t("flexibility.intro") }}</q-item-label>-->
                <q-card-section class="row q-col-gutter-sm q-px-none q-pt-sm q-pb-none">
                    <q-item-label caption>{{ t("flexibility.earlier") }}</q-item-label>
                    <q-select v-model="before" class="col" :options="options" emit-value map-options outlined dense />
                </q-card-section>

                <q-card-section class="row q-col-gutter-sm q-px-none q-pt-sm q-pb-none">
                    <q-item-label caption>{{ t("flexibility.later") }}</q-item-label>
                    <q-select
                        v-model="after"
                        class="col"
                        :options="options"
                        emit-value
                        map-options
                        outlined
                        dense
                    />
                </q-card-section>
            </q-card-section>
            <slot />
        </q-expansion-item>
    </q-card>
</template>
