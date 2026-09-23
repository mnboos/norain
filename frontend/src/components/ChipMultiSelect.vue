<script setup lang="ts">
import { symSharpCheckCircle, symSharpRadioButtonUnchecked } from "@quasar/extras/material-symbols-sharp";

/**
 * A multi-select dropdown whose picks sit in the field as removable chips. Each option carries an
 * emoji, shown on its chip and in the menu, so the choice reads at a glance without a grid of
 * checkboxes.
 */
export interface ChipOption {
    value: string;
    label: string;
    emoji?: string;
}

const model = defineModel<string[]>({ required: true });
defineProps<{ options: ChipOption[]; label: string; hint?: string }>();

function remove(value: string) {
    model.value = model.value.filter(v => v !== value);
}
</script>

<template>
    <q-select
        v-model="model"
        :options="options"
        :label="label"
        :hint="hint"
        option-value="value"
        option-label="label"
        emit-value
        map-options
        multiple
        outlined
        dense
        options-dense
    >
        <template #selected-item="{ opt }">
            <q-chip
                removable
                dense
                square
                color="primary"
                text-color="white"
                class="q-my-xs"
                :aria-label="opt.label"
                @remove="remove(opt.value)"
            >
                <span v-if="opt.emoji" class="q-mr-xs" aria-hidden="true">{{ opt.emoji }}</span>{{ opt.label }}
            </q-chip>
        </template>
        <template #option="{ opt, selected, itemProps }">
            <q-item v-bind="itemProps">
                <q-item-section v-if="opt.emoji" avatar class="text-h6" aria-hidden="true">{{ opt.emoji }}</q-item-section>
                <q-item-section>
                    <q-item-label>{{ opt.label }}</q-item-label>
                </q-item-section>
                <q-item-section side>
                    <q-icon :name="selected ? symSharpCheckCircle : symSharpRadioButtonUnchecked" :color="selected ? 'primary' : 'grey-5'" />
                </q-item-section>
            </q-item>
        </template>
    </q-select>
</template>
