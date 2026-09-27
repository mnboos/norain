<route lang="json5">
{
    name: "explore",
    meta: { title: "Entdecken" },
}
</route>

<script setup lang="ts">
import { ref } from "vue";
import { refDebounced } from "@vueuse/core";
import {
    symSharpChatBubble,
    symSharpFavorite,
    symSharpPhotoCamera,
    symSharpRoute,
    symSharpSearch,
} from "@quasar/extras/material-symbols-sharp";
import type { PublicRouteSummary } from "@norain/api/models";
import { mediaUrl, PublicRouteSort, usePublicRoutes } from "@/queries/publicRoutes";
import { pointsAttr, projectPath } from "@/utils/routeThumbnail";

const GLYPH_SIZE = 100;

const sort = ref(PublicRouteSort.New);
const search = ref<string | null>("");
const q = refDebounced(search, 300);
const { data: routes, isLoading, isError } = usePublicRoutes(sort, () => q.value ?? "");

const glyph = (route: PublicRouteSummary) => pointsAttr(projectPath(route.path ?? [], GLYPH_SIZE));
const km = (m: number) => `${(m / 1000).toFixed(1)} km`;
const minutes = (s: number) => {
    const total = Math.round(s / 60);
    return total >= 60 ? `${Math.floor(total / 60)} h ${String(total % 60).padStart(2, "0")}` : `${total} min`;
};
</script>

<template>
    <q-page class="q-pa-md">
        <div class="row items-center q-col-gutter-sm q-mb-md">
            <div class="col-12 col-sm">
                <h1 class="text-h5 text-weight-bold q-my-none">Entdecken</h1>
                <div class="text-caption text-muted">
                    Strecken, die andere teilen. Öffne eine und sieh das Wetter für deine eigene Abfahrt.
                </div>
            </div>
            <q-input
                v-model="search"
                dense
                outlined
                clearable
                placeholder="Suchen"
                class="col-12 col-sm-4"
                aria-label="Routen suchen"
            >
                <template #prepend><q-icon :name="symSharpSearch" /></template>
            </q-input>
            <q-btn-toggle
                v-model="sort"
                no-caps
                unelevated
                toggle-color="primary"
                aria-label="Sortierung"
                :options="[
                    { label: 'Neu', value: PublicRouteSort.New },
                    { label: 'Beliebt', value: PublicRouteSort.Popular },
                ]"
            />
        </div>

        <div v-if="isLoading" class="text-center q-mt-xl"><q-spinner-dots size="3rem" /></div>
        <q-banner v-else-if="isError" class="bg-tint-warn" rounded>Die Routen konnten nicht geladen werden.</q-banner>
        <div v-else-if="!routes?.length" class="text-center text-grey q-mt-xl">
            <q-icon :name="symSharpRoute" size="4rem" />
            <p class="q-mt-md text-body1">
                {{ q ? "Keine öffentliche Route passt zu deiner Suche." : "Noch keine öffentlichen Routen." }}
                Teile eine deiner Routen über „Teilen“ in der Routenansicht.
            </p>
        </div>

        <div v-else class="explore-grid">
            <q-card v-for="route in routes" :key="route.slug" flat bordered class="explore-card">
                <router-link :to="`/r/${route.slug}`" class="explore-link" :aria-label="route.name">
                    <div class="explore-cover">
                        <img v-if="route.coverUrl" :src="mediaUrl(route.coverUrl)" alt="" loading="lazy" />
                        <svg
                            v-else
                            :viewBox="`0 0 ${GLYPH_SIZE} ${GLYPH_SIZE}`"
                            class="explore-glyph"
                            aria-hidden="true"
                        >
                            <polyline
                                :points="glyph(route)"
                                fill="none"
                                stroke="currentColor"
                                stroke-width="2.5"
                                stroke-linejoin="round"
                            />
                        </svg>
                    </div>
                    <q-card-section>
                        <div class="text-subtitle1 text-weight-bold ellipsis">{{ route.name }}</div>
                        <div class="text-caption text-muted">
                            {{ km(route.distanceM) }} · {{ minutes(route.durationS) }}
                            <template v-if="route.ascentM != null">· {{ route.ascentM }} m ↑</template>
                            · von {{ route.author }}
                        </div>
                        <div class="row q-gutter-sm q-mt-xs text-caption text-muted">
                            <span>
                                <q-icon :name="symSharpFavorite" />
                                {{ route.likeCount ?? 0 }}
                            </span>
                            <span>
                                <q-icon :name="symSharpChatBubble" />
                                {{ route.commentCount ?? 0 }}
                            </span>
                            <span>
                                <q-icon :name="symSharpPhotoCamera" />
                                {{ route.photoCount ?? 0 }}
                            </span>
                        </div>
                    </q-card-section>
                </router-link>
            </q-card>
        </div>
    </q-page>
</template>

<style scoped>
.explore-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 12px;
}
.explore-card {
    overflow: hidden;
}
.explore-link {
    color: inherit;
    text-decoration: none;
    display: block;
}
.explore-cover {
    aspect-ratio: 16 / 10;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(127, 127, 127, 0.1);
    color: var(--q-primary);
}
.explore-cover img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
.explore-glyph {
    height: 80%;
}
</style>
