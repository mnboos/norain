<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { formatDistanceToNow } from "date-fns";
import { dateFnsLocale } from "@/i18n";
import { symSharpDelete, symSharpEdit } from "@quasar/extras/material-symbols-sharp";
import type { CommentOut } from "@norain/api/models";
import { useSession } from "@/composables/useSession";
import { useCommentMutations, useRouteComments } from "@/queries/publicRoutes";
import { apiErrorMessage } from "@/services/http";

const props = defineProps<{ slug: string }>();
const slug = computed(() => props.slug);
const currentRoute = useRoute();
const { isAuthenticated } = useSession();
const $q = useQuasar();
const { t } = useI18n();

const { data: comments, isLoading } = useRouteComments(slug);
const { add, edit, remove } = useCommentMutations(slug);

const draft = ref("");
const editingId = ref<string | null>(null);
const editDraft = ref("");

async function failed(error: unknown) {
    $q.notify({
        type: "negative",
        message: await apiErrorMessage(error, t("comments.failed")),
    });
}

async function submit() {
    const body = draft.value.trim();
    if (!body) return;
    try {
        await add.mutateAsync(body);
        draft.value = "";
    } catch (error) {
        await failed(error);
    }
}

function startEdit(comment: CommentOut) {
    editingId.value = comment.id;
    editDraft.value = comment.body;
}

async function saveEdit() {
    if (!editingId.value || !editDraft.value.trim()) return;
    try {
        await edit.mutateAsync({ id: editingId.value, body: editDraft.value.trim() });
        editingId.value = null;
    } catch (error) {
        await failed(error);
    }
}

const when = (date: Date) =>
    formatDistanceToNow(date, { addSuffix: true, locale: dateFnsLocale() });
</script>

<template>
    <section aria-labelledby="comments-heading">
        <h2 id="comments-heading" class="text-subtitle1 text-weight-bold q-my-sm">
            {{ t("comments.title") }}
            <span v-if="comments?.length">({{ comments.length }})</span>
        </h2>

        <q-skeleton v-if="isLoading" type="text" />
        <div v-else-if="!comments?.length" class="text-caption text-muted q-mb-sm">
            {{ t("comments.empty") }}
        </div>

        <q-list v-else separator>
            <q-item v-for="comment in comments" :key="comment.id" class="q-px-none">
                <q-item-section>
                    <q-item-label caption>
                        <strong>{{ comment.author }}</strong>
                        · {{ when(comment.createdAt) }}
                        <span v-if="comment.editedAt">· {{ t("comments.edited") }}</span>
                    </q-item-label>
                    <template v-if="editingId === comment.id">
                        <q-input
                            v-model="editDraft"
                            type="textarea"
                            autogrow
                            dense
                            outlined
                            maxlength="2000"
                            :aria-label="t('comments.edit')"
                        />
                        <div class="row q-gutter-sm q-mt-xs">
                            <q-btn
                                dense
                                no-caps
                                color="primary"
                                :label="t('common.save')"
                                :loading="edit.isPending.value"
                                @click="saveEdit"
                            />
                            <q-btn dense flat no-caps :label="t('common.cancel')" @click="editingId = null" />
                        </div>
                    </template>
                    <q-item-label v-else class="comment-body">{{ comment.body }}</q-item-label>
                </q-item-section>
                <q-item-section v-if="comment.canEdit || comment.canDelete" side top>
                    <div class="row no-wrap">
                        <q-btn
                            v-if="comment.canEdit && editingId !== comment.id"
                            flat
                            round
                            dense
                            size="sm"
                            :icon="symSharpEdit"
                            :aria-label="t('comments.edit')"
                            @click="startEdit(comment)"
                        />
                        <q-btn
                            v-if="comment.canDelete"
                            flat
                            round
                            dense
                            size="sm"
                            :icon="symSharpDelete"
                            :aria-label="t('comments.delete')"
                            :loading="remove.isPending.value && remove.variables.value?.id === comment.id"
                            @click="remove.mutate(comment, { onError: failed })"
                        />
                    </div>
                </q-item-section>
            </q-item>
        </q-list>

        <form v-if="isAuthenticated" class="q-mt-md" @submit.prevent="submit">
            <q-input
                v-model="draft"
                type="textarea"
                autogrow
                outlined
                dense
                maxlength="2000"
                :label="t('comments.write')"
            />
            <q-btn
                type="submit"
                color="primary"
                no-caps
                :label="t('comments.send')"
                class="q-mt-sm"
                :disable="!draft.trim()"
                :loading="add.isPending.value"
            />
        </form>
        <q-btn
            v-else
            outline
            no-caps
            class="q-mt-md"
            :label="t('comments.signIn')"
            :to="{ path: '/account', query: { next: currentRoute.fullPath } }"
        />
    </section>
</template>

<style scoped>
.comment-body {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
</style>
