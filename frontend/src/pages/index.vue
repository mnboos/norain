<route lang="json5">
{
    name: "dashboard",
    meta: { title: "Dashboard", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuasar } from "quasar";
import { useRouter } from "vue-router";
import type { RecurringRouteIn, RecurringRouteOut } from "@norain/api/models";
import RouteListPanel from "@/components/RouteListPanel.vue";
import RouteFormDialog from "@/components/RouteFormDialog.vue";
import { useEntitlements } from "@/composables/useEntitlements";
import { isQuotaExceeded } from "@/services/http";
import { useCreateRecurringRoute, useDeleteRecurringRoute, useRecurringRoutes } from "@/queries/recurringRoutes";

const $q = useQuasar();
const router = useRouter();
const showAddDialog = ref(false);
const { maxRoutes, atRouteLimit } = useEntitlements();

const { data: routes, isLoading } = useRecurringRoutes();

const routesList = computed<RecurringRouteOut[]>(() => routes.value ?? []);

const createMutation = useCreateRecurringRoute();
const deleteMutation = useDeleteRecurringRoute();

function onRouteSave(data: RecurringRouteIn) {
    createMutation.mutate(data, {
        onError: (err: unknown) => {
            // The server enforces the quota; 402 is it saying the tier is full.
            if (isQuotaExceeded(err)) {
                $q.dialog({
                    title: "Tarifgrenze erreicht",
                    message: `Der Free-Tarif erlaubt ${maxRoutes.value ?? 2} aktive Routen. Mit Pro sind es unbegrenzt viele.`,
                    cancel: { label: "Später", flat: true },
                    ok: { label: "Upgrade", color: "primary", unelevated: true },
                }).onOk(() => void router.push("/account"));
                return;
            }
            $q.notify({ type: "negative", message: "Route konnte nicht erstellt werden." });
        },
    });
}

function onRouteDelete(id: string) {
    const route = routesList.value.find(r => r.id === id);
    $q.dialog({
        title: "Route löschen",
        message: `Möchtest du die Route „${route?.name ?? ""}“ wirklich löschen?`,
        cancel: { label: "Abbrechen", flat: true },
        ok: { label: "Löschen", color: "negative", unelevated: true },
        persistent: true,
    }).onOk(() => {
        deleteMutation.mutate(id);
    });
}
</script>

<template>
    <q-page class="row justify-center">
        <RouteListPanel
            :routes="routesList"
            :loading="isLoading"
            :at-route-limit="atRouteLimit"
            :max-routes="maxRoutes"
            @add="showAddDialog = true"
            @delete="onRouteDelete"
            @upgrade="router.push('/account')"
        />
        <RouteFormDialog v-model="showAddDialog" @save="onRouteSave" />
    </q-page>
</template>
