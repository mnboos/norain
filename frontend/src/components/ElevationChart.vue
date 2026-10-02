<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from "vue";
import { useQueries, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { tp } from "@/i18n";
import { ElevationApi, PublicRoutesApi } from "@norain/api/apis";
import type { ElevationOut } from "@norain/api/models";
import { ELEVATION_PRIMARY_GROUP, elevationFigure, type ElevationSeries } from "@/utils/elevation";
import { interpolate } from "@/utils/forecastSelection";

const NiceChart = defineAsyncComponent(() => import("./chart/NiceChart.vue"));
const { t } = useI18n();
const props = defineProps<{
    routeId?: string;
    stageId?: string;
    /** A public route's profile: its line between the privacy zones only. */
    publicSlug?: string;
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
    /** The selected route position, a share (0..1) of the route's distance. */
    position?: number;
    /** The routing profile, which words the time axis (a hike walks). */
    profile?: string | null;
}>();
const emit = defineEmits<{ selectPosition: [position: number] }>();
const api = new ElevationApi();
const publicApi = new PublicRoutesApi();
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
                  props.publicSlug,
                  props.version,
                  props.coordinates,
                  props.totalSeconds,
                  props.vertexTimes,
              ],
    ),
    enabled: () =>
        !!props.routeId ||
        !!props.stageId ||
        !!props.publicSlug ||
        (!!props.coordinates?.length && !!props.totalSeconds),
    queryFn: () => {
        if (props.publicSlug) return publicApi.coreApiCommunityPublicRouteElevation({ slug: props.publicSlug });
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
            label: props.label ?? t("elevation.height"),
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
// The position is a share of the distance; the axis is km or minutes of this route's own profile.
const primaryPoints = computed(() => (usable(query.data.value) ? (query.data.value?.points ?? []) : []));
const distances = computed(() => primaryPoints.value.map(p => p.distanceM));
const totalDistance = computed(() => distances.value[distances.value.length - 1] ?? 0);
const cursorX = computed(() => {
    if (props.position === undefined || !(totalDistance.value > 0)) return undefined;
    const distance = props.position * totalDistance.value;
    if (axis.value === "distance") return distance / 1000;
    const elapsed = interpolate(
        distance,
        distances.value,
        primaryPoints.value.map(p => p.elapsedS),
    );
    return elapsed === undefined ? undefined : elapsed / 60;
});
function selectX(x: number) {
    if (!(totalDistance.value > 0)) return;
    const distance =
        axis.value === "distance"
            ? x * 1000
            : interpolate(
                  x * 60,
                  primaryPoints.value.map(p => p.elapsedS),
                  distances.value,
              );
    if (distance !== undefined) emit("selectPosition", Math.max(0, Math.min(1, distance / totalDistance.value)));
}
const figure = computed(() => {
    const series: ElevationSeries[] = profiles.value.map(p => ({ ...p, points: p.data.points }));
    return elevationFigure(series, axis.value, props.profile);
});
</script>

<template>
    <q-card class="column overflow-hidden transparent" flat>
        <q-card-section>
            <q-item-label>
                {{ t("elevation.title") }}
            </q-item-label>
        </q-card-section>
        <!--        <q-card-section class="row">-->
        <!--            <div class="text-subtitle2">{{ t("elevation.title") }}</div>-->
        <!--            <q-btn-toggle-->
        <!--                v-model="axis"-->
        <!--                dense-->
        <!--                flat-->
        <!--                no-caps-->
        <!--                :aria-label="t('elevation.axis')"-->
        <!--                :options="[-->
        <!--                    { label: t('elevation.distance'), value: 'distance' },-->
        <!--                    { label: tp(profile, 'timing.duration'), value: 'time' },-->
        <!--                ]"-->
        <!--            />-->
        <!--        </q-card-section>-->
        <q-card-section class="col no-padding column">
            <NiceChart
                v-if="hasData"
                :figure="figure"
                keep-line-widths
                :cursor-x="cursorX"
                :cursor-group="ELEVATION_PRIMARY_GROUP"
                :x-unit="axis === 'distance' ? 'km' : 'min'"
                :class="{ 'compact-elevation-plot': compact }"
                class="col column"
                @cursor="selectX"
            />
            <q-skeleton
                v-else-if="pending"
                :height="props.compact ? '180px' : '220px'"
                :aria-label="t('elevation.loading')"
            />
            <div v-else-if="failed.length" role="alert" class="q-pa-md">
                {{ t("elevation.loadFailed") }}
                <q-btn flat no-caps :label="t('common.retry')" @click="retryFailed" />
            </div>
            <div v-else class="q-pa-md">{{ t("elevation.noData") }}</div>
        </q-card-section>
        <template v-if="hasData">
            <div v-if="query.isError.value" role="alert" class="q-pa-md">
                {{ t("elevation.selectedLoadFailed") }}
                <q-btn flat no-caps :label="t('common.retry')" @click="query.refetch()" />
            </div>
            <div v-else-if="query.isPending.value" role="status" class="q-pa-md">
                {{ t("elevation.selectedLoading") }}
            </div>
            <div v-else-if="!usable(query.data.value)" class="q-pa-md">
                {{ t("elevation.selectedNoData") }}
            </div>
        </template>
        <div v-if="hasData" class="text-caption text-muted">
            <span v-if="axis === 'time' && approximateTiming">{{ tp(profile, "elevation.approximateTiming") }}</span>
            <span v-if="partialHeights">· {{ t("elevation.partial") }}</span>
        </div>
        <div v-if="$slots.footer" class="text-caption text-muted"><slot name="footer" /></div>
    </q-card>
</template>

<style scoped>
.compact-elevation {
    /*    display: flex;
    flex-direction: column;
    !*min-height: 280px;*!
    min-width: 0;*/
}
.compact-elevation-plot {
    /*    flex: 1 1 180px;
    min-height: 180px;*/
}
</style>
