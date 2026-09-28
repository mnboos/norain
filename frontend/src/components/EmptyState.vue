<script setup lang="ts">
import { symSharpRoute } from "@quasar/extras/material-symbols-sharp";
import { useI18n } from "vue-i18n";

const { t } = useI18n();
</script>

<template>
    <div class="text-center text-grey q-mt-xl">
        <q-icon :name="symSharpRoute" size="4rem" />
        <p class="q-mt-md text-body1">{{ t("routes.empty") }}</p>
    </div>
</template>
