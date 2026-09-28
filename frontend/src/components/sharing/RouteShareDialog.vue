<script setup lang="ts">
import { computed, ref } from "vue";
import { copyToClipboard, useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import {
    symSharpAddPhotoAlternate,
    symSharpContentCopy,
    symSharpDelete,
    symSharpLocationOn,
    symSharpShare,
} from "@quasar/extras/material-symbols-sharp";
import { SharingInVisibilityEnum as Visibility, SharingOutVisibilityEnum, type PhotoOut } from "@norain/api/models";
import GpxPreviewMap from "@/components/GpxPreviewMap.vue";
import {
    mediaUrl,
    publicRouteLink,
    usePhotoMutations,
    useRoutePhotos,
    useSharing,
    useUpdateSharing,
} from "@/queries/publicRoutes";
import { apiErrorMessage } from "@/services/http";
import { readExifGps } from "@/utils/exifGps";

const props = defineProps<{ routeId: string; routeName: string }>();
const open = defineModel<boolean>({ required: true });
const $q = useQuasar();
const { t } = useI18n();
const routeId = computed(() => props.routeId);

const { data: sharing } = useSharing(routeId);
const update = useUpdateSharing(routeId);
const { data: photos } = useRoutePhotos(routeId);
const { upload, update: updatePhoto, remove } = usePhotoMutations(routeId);

const ZONES = computed(() => [
    { label: t("share.zoneOff"), value: 0 },
    { label: "250 m", value: 250 },
    { label: "500 m", value: 500 },
    { label: "1 km", value: 1000 },
    { label: "2 km", value: 2000 },
]);

const isPublic = computed(() => sharing.value?.visibility === SharingOutVisibilityEnum.Public);
const link = computed(() =>
    sharing.value?.publicSlug && isPublic.value ? publicRouteLink(sharing.value.publicSlug) : "",
);

async function notifyError(error: unknown, fallback: string) {
    $q.notify({ type: "negative", message: await apiErrorMessage(error, fallback) });
}

function save(visibility: Visibility, privacyZoneM: number) {
    update.mutate(
        { visibility, privacyZoneM },
        { onError: error => void notifyError(error, t("share.saveFailed")) },
    );
}

async function share() {
    if (!link.value) return;
    // Missing on most desktop browsers, whatever the DOM types say.
    if ("share" in navigator) {
        await navigator.share({ title: props.routeName, url: link.value }).catch(() => undefined);
        return;
    }
    await copyToClipboard(link.value);
    $q.notify({ type: "positive", message: t("publicRoute.linkCopied") });
}

async function copyLink() {
    await copyToClipboard(link.value);
    $q.notify({ type: "positive", message: t("publicRoute.linkCopied") });
}

// -- photos -----------------------------------------------------------------------

const files = ref<File[] | null>(null);
/** The only way a position reaches the server: the file itself is re-encoded without it. */
const usePosition = ref(true);
const uploading = ref(0);

async function uploadFiles(selected: File[] | File | null) {
    const list = Array.isArray(selected) ? selected : selected ? [selected] : [];
    files.value = null;
    for (const file of list) {
        uploading.value++;
        try {
            const position = usePosition.value ? readExifGps(await file.arrayBuffer()) : null;
            await upload.mutateAsync({ file, ...position });
        } catch (error) {
            await notifyError(error, t("share.uploadFailed", { name: file.name }));
        } finally {
            uploading.value--;
        }
    }
}

const captions = ref<Record<string, string>>({});
function captionOf(photo: PhotoOut) {
    return captions.value[photo.id] ?? photo.caption;
}
function saveCaption(photo: PhotoOut) {
    const caption = captionOf(photo).trim();
    if (caption === photo.caption) return;
    updatePhoto.mutate(
        { id: photo.id, caption, lat: photo.lat, lon: photo.lon },
        { onError: error => void notifyError(error, t("share.captionFailed")) },
    );
}
function forgetPosition(photo: PhotoOut) {
    updatePhoto.mutate({ id: photo.id, caption: photo.caption, lat: null, lon: null });
}
</script>

<template>
    <q-dialog v-model="open">
        <q-card style="width: 640px; max-width: 95vw">
            <q-card-section>
                <div class="text-h6">{{ t("share.title") }}</div>
            </q-card-section>

            <q-card-section class="q-pt-none">
                <q-toggle
                    :model-value="isPublic"
                    :label="t('share.public')"
                    :disable="!sharing || update.isPending.value"
                    @update:model-value="
                        save($event ? Visibility.Public : Visibility.Private, sharing?.privacyZoneM ?? 500)
                    "
                />

                <div class="q-mt-md text-subtitle2">{{ t("share.zoneTitle") }}</div>
                <q-btn-toggle
                    :model-value="sharing?.privacyZoneM ?? 500"
                    :options="ZONES"
                    no-caps
                    unelevated
                    toggle-color="primary"
                    :disable="!sharing || update.isPending.value"
                    :aria-label="t('share.zone')"
                    @update:model-value="save(isPublic ? Visibility.Public : Visibility.Private, $event)"
                />
                <p class="text-caption text-muted q-mt-sm q-mb-none">
                    {{ t("share.zoneHint") }}
                    <template v-if="sharing?.publicDistanceM">
                        {{ t("share.visible", { km: (sharing.publicDistanceM / 1000).toFixed(1) }) }}
                    </template>
                    <strong v-else-if="sharing">{{ t("share.tooLittle") }}</strong>
                </p>
                <GpxPreviewMap v-if="sharing?.publicLine" :original="sharing.publicLine" class="q-mt-sm" />

                <div v-if="link" class="row items-center q-gutter-sm q-mt-md">
                    <q-input :model-value="link" readonly dense outlined class="col" :aria-label="t('share.link')" />
                    <q-btn flat round :icon="symSharpContentCopy" :aria-label="t('share.copyLink')" @click="copyLink" />
                    <q-btn flat round :icon="symSharpShare" :aria-label="t('gpx.share')" @click="share" />
                </div>
            </q-card-section>

            <q-separator />

            <q-card-section>
                <div class="text-subtitle2">{{ t("publicRoute.photos") }}</div>
                <p class="text-caption text-muted q-mb-sm">{{ t("share.photosHint") }}</p>
                <q-file
                    v-model="files"
                    multiple
                    accept="image/jpeg,image/png,image/webp"
                    outlined
                    dense
                    :label="t('share.addPhotos')"
                    :loading="uploading > 0"
                    @update:model-value="uploadFiles"
                >
                    <template #prepend><q-icon :name="symSharpAddPhotoAlternate" /></template>
                </q-file>
                <q-checkbox v-model="usePosition" dense :label="t('share.usePosition')" class="q-mt-xs" />

                <q-list v-if="photos?.length" separator class="q-mt-sm">
                    <q-item v-for="photo in photos" :key="photo.id" class="q-px-none">
                        <q-item-section avatar>
                            <img :src="mediaUrl(photo.thumbnailUrl)" alt="" class="photo-thumb" />
                        </q-item-section>
                        <q-item-section>
                            <q-input
                                :model-value="captionOf(photo)"
                                dense
                                borderless
                                maxlength="500"
                                :placeholder="t('share.caption')"
                                :aria-label="t('share.caption')"
                                @update:model-value="captions[photo.id] = String($event ?? '')"
                                @blur="saveCaption(photo)"
                                @keyup.enter="saveCaption(photo)"
                            />
                            <q-item-label v-if="photo.lat != null" caption>
                                <q-icon :name="symSharpLocationOn" />
                                {{ t("share.onMap") }} ·
                                <a href="#" @click.prevent="forgetPosition(photo)">{{ t("share.remove") }}</a>
                            </q-item-label>
                        </q-item-section>
                        <q-item-section side>
                            <q-btn
                                flat
                                round
                                dense
                                :icon="symSharpDelete"
                                :aria-label="t('share.deletePhoto')"
                                @click="
                                    remove.mutate(photo.id, {
                                        onError: e => void notifyError(e, t('share.deleteFailed')),
                                    })
                                "
                            />
                        </q-item-section>
                    </q-item>
                </q-list>
            </q-card-section>

            <q-card-actions align="right">
                <q-btn v-if="link" flat no-caps :label="t('share.viewPublic')" :to="`/r/${sharing?.publicSlug}`" />
                <q-btn v-close-popup flat no-caps :label="t('share.done')" color="primary" />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>

<style scoped>
.photo-thumb {
    width: 56px;
    height: 56px;
    object-fit: cover;
    border-radius: 4px;
}
</style>
