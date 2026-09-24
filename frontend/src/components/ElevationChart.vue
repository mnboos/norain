<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from "vue";
import { useQueries, useQuery } from "@tanstack/vue-query";
import { ElevationApi } from "@norain/api/apis";
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
const hasData = computed(() => query.data.value?.points.some(p => p.elevationM !== null));
const figure = computed(() => {
    const series: ElevationSeries[] = (props.alternatives ?? []).flatMap((a, n) => {
        const points = alternativeQueries.value[n]?.data?.points;
        return points ? [{ points, color: a.color, label: a.label }] : [];
    });
    series.push({
        points: query.data.value?.points ?? [],
        color: props.color ?? "#32966b",
        label: props.label ?? "Höhe",
        primary: true,
    });
    return elevationFigure(series, axis.value);
});
</script>

<template>
    <q-card flat bordered class="q-pa-sm">
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
        <q-skeleton v-if="query.isPending.value" height="220px" aria-label="Höhenprofil wird geladen" />
        <div v-else-if="query.isError.value" role="alert" class="q-pa-md">
            Höhendaten konnten nicht geladen werden.
            <q-btn flat no-caps label="Erneut versuchen" @click="query.refetch()" />
        </div>
        <div v-else-if="!hasData" class="q-pa-md">Keine Höhendaten für diese Strecke verfügbar.</div>
        <NiceChart
            v-else
            :figure="figure"
            keep-line-widths
            :x-unit="axis === 'distance' ? 'km' : 'min'"
            style="height: 260px"
        />
        <div v-if="hasData" class="text-caption text-muted">
            {{ query.data.value?.source }}
            <span v-if="axis === 'time' && query.data.value?.approximateTiming">
                · Fahrzeit nach Streckenlänge geschätzt
            </span>
            <span v-if="query.data.value?.points.some(p => p.elevationM === null)">
                · Höhendaten teilweise nicht verfügbar
            </span>
        </div>
    </q-card>
</template>
