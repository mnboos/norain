<route lang="json5">
{
    name: "dashboard",
    path: "/routes",
    meta: { titleKey: "pages.dashboard", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import type { RecurringRouteIn, RecurringRouteOut } from "@norain/api/models";
import RouteListPanel from "@/components/RouteListPanel.vue";
import RouteFormDialog from "@/components/RouteFormDialog.vue";
import { useEntitlements } from "@/composables/useEntitlements";
import { usePrefetchRoutes } from "@/composables/usePrefetchRoutes";
import { isQuotaExceeded } from "@/services/http";
import { useCreateRecurringRoute, useDeleteRecurringRoute, useRecurringRoutes } from "@/queries/recurringRoutes";

const $q = useQuasar();
const { t } = useI18n();
const router = useRouter();
const showAddDialog = ref(false);
const { maxRoutes, atRouteLimit } = useEntitlements();

const { data: routes, isLoading } = useRecurringRoutes();
// Load the likely next routes' forecasts now, so opening one shows it at once.
usePrefetchRoutes(routes);

const routesList = computed<RecurringRouteOut[]>(() => routes.value ?? []);

const createMutation = useCreateRecurringRoute();
const deleteMutation = useDeleteRecurringRoute();

function onRouteSave(data: RecurringRouteIn) {
    createMutation.mutate(data, {
        onError: (err: unknown) => {
            // The server enforces the quota; 402 is it saying the tier is full.
            if (isQuotaExceeded(err)) {
                $q.dialog({
                    title: t("quota.title"),
                    message: t("quota.routes", maxRoutes.value ?? 2),
                    cancel: { label: t("quota.later"), flat: true },
                    ok: { label: t("quota.upgrade"), color: "primary", unelevated: true },
                }).onOk(() => void router.push("/account"));
                return;
            }
            $q.notify({ type: "negative", message: t("routes.createFailed") });
        },
    });
}

function onRouteDelete(id: string) {
    const route = routesList.value.find(r => r.id === id);
    $q.dialog({
        title: t("routes.delete.title"),
        message: t("routes.delete.message", { name: route?.name ?? "" }),
        cancel: { label: t("common.cancel"), flat: true },
        ok: { label: t("common.delete"), color: "negative", unelevated: true },
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
