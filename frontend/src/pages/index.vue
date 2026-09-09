<route lang="json5">
{
  name: "dashboard",
  meta: { title: "Dashboard" }
}
</route>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuasar } from "quasar";
import { useQuery, useMutation, useQueryClient } from "@tanstack/vue-query";
import { DefaultApi } from "@norain/api";
import type { RecurringRouteIn, RecurringRouteOut } from "@norain/api";
import RouteListPanel from "@/components/RouteListPanel.vue";
import RouteFormDialog from "@/components/RouteFormDialog.vue";

const api = new DefaultApi();
const $q = useQuasar();
const queryClient = useQueryClient();
const showAddDialog = ref(false);

const { data: routes, isLoading } = useQuery({
    queryKey: ["routes"],
    queryFn: () => api.coreRoutesApiListRoutes(),
    refetchInterval: 60_000,
    staleTime: 30_000,
});

const routesList = computed<RecurringRouteOut[]>(() => routes.value ?? []);

const createMutation = useMutation({
    mutationFn: (data: RecurringRouteIn) => api.coreRoutesApiCreateRoute({ recurringRouteIn: data }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["routes"] }),
});

const deleteMutation = useMutation({
    mutationFn: (id: string) => api.coreRoutesApiDeleteRoute({ routeId: id }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["routes"] }),
});

function onRouteSave(data: RecurringRouteIn) {
    createMutation.mutate(data);
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
        <div class="col" style="max-width: 480px">
            <RouteListPanel
                :routes="routesList"
                :loading="isLoading"
                @add="showAddDialog = true"
                @delete="onRouteDelete"
            />
        </div>
        <RouteFormDialog v-model="showAddDialog" @save="onRouteSave" />
    </q-page>
</template>
