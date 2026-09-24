<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from "vue";
import { useQueries, useQuery } from "@tanstack/vue-query";
import { ElevationApi } from "@norain/api/apis";
import type { ElevationOut } from "@norain/api/models";
import { elevationFigure, type ElevationSeries } from "@/utils/elevation";

const NiceChart = defineAsyncComponent(() => import("./chart/NiceChart.vue"));
const props = defineProps<{
    routeId?: string;
    stageId?: string;
    version?: string;
    coordinates?: number[][];
    totalSeconds?: number;
    vertexTimes?: number[] | null;
    color?: string;
    label?: string;
    /** Other routes drawn beside this one, each in its own colour (a journey day's variants). */
    alternatives?: { stageId: string; color: string; label: string }[];
    /** Compact card sizing for side-by-side journey charts. */
    compact?: boolean;
}>();
const api = new ElevationApi();
const axis = ref<"distance" | "time">("distance");
const STALE_TIME = 60 * 60 * 1000;
// One key per stage, whether it is the main line or an alternative, so switching variants
// finds the new main line in the cache.
const stageKey = (stageId: string) => ["elevation", "stage", stageId];
const query = useQuery({
    queryKey: computed(() =>
        props.stageId && !props.routeId
            ? stageKey(props.stageId)
            : [
                  "elevation",
                  props.routeId,
                  props.stageId,
                  props.version,
                  props.coordinates,
                  props.totalSeconds,
                  props.vertexTimes,
              ],
    ),
    enabled: () => !!props.routeId || !!props.stageId || (!!props.coordinates?.length && !!props.totalSeconds),
    queryFn: () => {
        if (props.routeId) return api.coreApiElevationRouteElevation({ routeId: props.routeId });
        if (props.stageId) return api.coreApiElevationStageElevation({ stageId: props.stageId });
        if (!props.coordinates || !props.totalSeconds) throw new Error("Route is not ready");
        return api.coreApiElevationPreviewElevation({
            elevationIn: {
                coordinates: props.coordinates,
                totalSeconds: props.totalSeconds,
                vertexTimes: props.vertexTimes,
            },
        });
    },
    staleTime: STALE_TIME,
    retry: 1,
});
// Alternatives never hold up the main line: each appears once loaded, a failed one is left out.
const alternativeQueries = useQueries({
    queries: computed(() =>
        (props.alternatives ?? []).map(a => ({
            queryKey: stageKey(a.stageId),
            queryFn: () => api.coreApiElevationStageElevation({ stageId: a.stageId }),
            staleTime: STALE_TIME,
            retry: 1,
        })),
    ),
});
const usable = (data?: ElevationOut) => !!data?.points.some(p => p.elevationM != null);
const profiles = computed(() =>
    [
        ...(props.alternatives ?? []).map((a, n) => ({
            data: alternativeQueries.value[n]?.data,
            color: a.color,
            label: a.label,
            primary: false,
        })),
        {
            data: query.data.value,
            color: props.color ?? "#32966b",
            label: props.label ?? "Höhe",
            primary: true,
        },
    ].filter((p): p is typeof p & { data: ElevationOut } => usable(p.data)),
);
const hasData = computed(() => profiles.value.length > 0);
const results = computed(() => [
    {
        isPending: query.isPending.value,
        fetchStatus: query.fetchStatus.value,
        isError: query.isError.value,
        refetch: query.refetch,
    },
    ...alternativeQueries.value,
]);
const pending = computed(() => results.value.some(q => q.isPending && q.fetchStatus !== "idle"));
const failed = computed(() => results.value.filter(q => q.isError));
function retryFailed() {
    for (const result of failed.value) void result.refetch();
}
const sources = computed(() => [...new Set(profiles.value.map(p => p.data.source))].join(" · "));
const approximateTiming = computed(() => profiles.value.some(p => p.data.approximateTiming));
const partialHeights = computed(() => profiles.value.some(p => p.data.points.some(point => point.elevationM == null)));
const figure = computed(() => {
    const series: ElevationSeries[] = profiles.value.map(p => ({ ...p, points: p.data.points }));
    return elevationFigure(series, axis.value);
});
</script>

<template>
    <q-card flat bordered class="q-pa-sm" :class="{ 'compact-elevation': compact }">
        <div class="row items-center justify-between q-gutter-sm">
            <div class="text-subtitle2">Höhenprofil</div>
            <q-btn-toggle
                v-model="axis"
                dense
                flat
                no-caps
                aria-label="Achse des Höhenprofils"
                :options="[
                    { label: 'Strecke', value: 'distance' },
                    { label: 'Fahrzeit', value: 'time' },
                ]"
            />
        </div>
        <NiceChart
            v-if="hasData"
            :figure="figure"
            keep-line-widths
            :x-unit="axis === 'distance' ? 'km' : 'min'"
            :class="{ 'compact-elevation-plot': compact }"
            :style="compact ? undefined : { height: '260px' }"
        />
        <q-skeleton
            v-else-if="pending"
            :height="props.compact ? '180px' : '220px'"
            aria-label="Höhenprofil wird geladen"
        />
        <div v-else-if="failed.length" role="alert" class="q-pa-md">
            Höhendaten konnten nicht geladen werden.
            <q-btn flat no-caps label="Erneut versuchen" @click="retryFailed" />
        </div>
        <div v-else class="q-pa-md">Keine Höhendaten für diese Strecke verfügbar.</div>
        <template v-if="hasData">
            <div v-if="query.isError.value" role="alert" class="q-pa-md">
                Höhendaten für die gewählte Strecke konnten nicht geladen werden.
                <q-btn flat no-caps label="Erneut versuchen" @click="query.refetch()" />
            </div>
            <div v-else-if="query.isPending.value" role="status" class="q-pa-md">
                Höhenprofil für die gewählte Strecke wird geladen…
            </div>
            <div v-else-if="!usable(query.data.value)" class="q-pa-md">
                Keine Höhendaten für die gewählte Strecke verfügbar.
            </div>
        </template>
        <div v-if="hasData" class="text-caption text-muted">
            {{ sources }}
            <span v-if="axis === 'time' && approximateTiming">· Fahrzeit nach Streckenlänge geschätzt</span>
            <span v-if="partialHeights">· Höhendaten teilweise nicht verfügbar</span>
        </div>
        <div v-if="$slots.footer" class="text-caption text-muted"><slot name="footer" /></div>
    </q-card>
</template>

<style scoped>
.compact-elevation {
    display: flex;
    flex-direction: column;
    min-height: 280px;
    min-width: 0;
}
.compact-elevation-plot {
    flex: 1 1 180px;
    min-height: 180px;
}
</style>
