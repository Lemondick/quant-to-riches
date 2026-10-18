<template>
  <dialog ref="dialog" :class="['app-dialog', { 'is-drawer': drawer }]" :aria-labelledby="titleId" @close="$emit('update:modelValue', false)" @cancel="$emit('update:modelValue', false)">
    <div class="dialog-head"><div><h2 :id="titleId">{{ title }}</h2><p v-if="description">{{ description }}</p></div><button class="icon-btn" type="button" aria-label="关闭弹窗" @click="$emit('update:modelValue', false)"><X :size="18" aria-hidden="true" /></button></div>
    <div class="dialog-body"><slot /></div>
    <div v-if="$slots.footer" class="dialog-foot"><slot name="footer" /></div>
  </dialog>
</template>
<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, useId, watch } from 'vue'
import { X } from 'lucide-vue-next'
const props = defineProps<{ modelValue: boolean; title: string; description?: string; drawer?: boolean }>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()
const dialog = ref<HTMLDialogElement>()
const titleId = useId()
function sync() {
  if (props.modelValue && !dialog.value?.open) dialog.value?.showModal()
  if (!props.modelValue && dialog.value?.open) dialog.value.close()
}
watch(() => props.modelValue, sync, { flush: 'post' })
onMounted(sync)
onBeforeUnmount(() => dialog.value?.close())
</script>
