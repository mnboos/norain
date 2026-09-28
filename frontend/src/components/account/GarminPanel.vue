<script setup lang="ts">
import { ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { garminApi } from "@/services/garmin";

const { t } = useI18n();
const queryClient = useQueryClient();
const token = ref("");
const busy = ref(false);
const error = ref("");

const state = useQuery({
    queryKey: ["garminToken"],
    queryFn: garminApi.state,
});
async function change(method: "POST" | "DELETE") {
    busy.value = true;
    error.value = "";
    try {
        const result = await garminApi.change(method);
        token.value = result.token;
        // Keep the raw credential only in this mounted component, never in the query cache.
        queryClient.setQueryData(["garminToken"], { connected: result.connected, token: "" });
    } catch (cause) {
        error.value = cause instanceof Error ? cause.message : String(cause);
    } finally {
        busy.value = false;
    }
}
</script>

<template>
    <section class="q-mb-lg">
        <div class="text-h6">{{ t("garmin.title") }}</div>
        <p>{{ t("garmin.intro") }}</p>
        <p v-if="state.data.value?.connected" class="text-caption">{{ t("garmin.connected") }}</p>
        <q-banner v-if="error || state.error.value" class="bg-negative text-white q-mb-sm">
            {{ error || state.error.value?.message }}
        </q-banner>
        <q-input v-if="token" :model-value="token" :label="t('garmin.token')" readonly outlined class="q-mb-sm" />
        <p v-if="token" class="text-caption">{{ t("garmin.help") }}</p>
        <q-btn color="primary" :loading="busy" :label="t(state.data.value?.connected ? 'garmin.replace' : 'garmin.create')" no-caps @click="change('POST')" />
        <q-btn v-if="state.data.value?.connected" class="q-ml-sm" :disable="busy" :label="t('garmin.revoke')" flat no-caps @click="change('DELETE')" />
    </section>
</template>
