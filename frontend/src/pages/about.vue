<route lang="json5">
{
  name: "about",
  meta: { titleKey: "pages.about" }
}
</route>

<script setup lang="ts">
import { useI18n } from "vue-i18n";

const { t } = useI18n();
</script>

<template>
    <div class="about">
        <h1>{{ t("pages.about") }}</h1>
    </div>
</template>

<style>
@media (min-width: 1024px) {
    .about {
        min-height: 100vh;
        display: flex;
        align-items: center;
    }
}
</style>
