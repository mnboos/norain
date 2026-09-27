<script setup lang="ts">
import { ref } from "vue";
import { symSharpClose } from "@quasar/extras/material-symbols-sharp";
import type { PhotoOut } from "@norain/api/models";
import { mediaUrl } from "@/queries/publicRoutes";

const props = defineProps<{ photos: PhotoOut[] }>();
const open = ref(false);
const current = ref("");

function show(photo: PhotoOut) {
    current.value = photo.id;
    open.value = true;
}

defineExpose({
    /** Opens the viewer on a photo, e.g. from its marker on the map. */
    show(id: string) {
        const photo = props.photos.find(p => p.id === id);
        if (photo) show(photo);
    },
});
</script>

<template>
    <div v-if="photos.length" class="photo-grid" role="list" aria-label="Fotos">
        <button
            v-for="photo in photos"
            :key="photo.id"
            type="button"
            role="listitem"
            class="photo-tile"
            :aria-label="photo.caption || 'Foto ansehen'"
            @click="show(photo)"
        >
            <img :src="mediaUrl(photo.thumbnailUrl)" :alt="photo.caption" loading="lazy" />
        </button>
    </div>

    <q-dialog v-model="open" maximized transition-show="fade" transition-hide="fade">
        <q-card class="bg-black text-white column no-wrap">
            <q-bar class="bg-black">
                <q-space />
                <q-btn v-close-popup dense flat round :icon="symSharpClose" aria-label="Schliessen" />
            </q-bar>
            <q-carousel
                v-model="current"
                animated
                swipeable
                infinite
                arrows
                navigation
                class="bg-black col"
                control-color="white"
            >
                <q-carousel-slide v-for="photo in photos" :key="photo.id" :name="photo.id" class="column no-wrap q-pb-xl">
                    <img :src="mediaUrl(photo.url)" :alt="photo.caption" class="col photo-full" />
                    <div v-if="photo.caption" class="text-center q-pa-sm">{{ photo.caption }}</div>
                </q-carousel-slide>
            </q-carousel>
        </q-card>
    </q-dialog>
</template>

<style scoped>
.photo-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
    gap: 6px;
}
.photo-tile {
    padding: 0;
    border: 0;
    border-radius: 6px;
    overflow: hidden;
    aspect-ratio: 1;
    cursor: zoom-in;
    background: var(--q-dark, #222);
}
.photo-tile img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
.photo-full {
    object-fit: contain;
    min-height: 0;
    width: 100%;
}
</style>
