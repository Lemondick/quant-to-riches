<template>
  <div class="filter-toolbar">
    <div class="filter-toolbar-main">
      <div class="category-tabs" role="group" :aria-label="label">
        <button v-for="option in options" :key="option" type="button" :class="{ active: modelValue === option }" :aria-pressed="modelValue === option" @click="$emit('update:modelValue', option)">{{ option }}</button>
      </div>
      <label class="searchbox filter-search">
        <Search :size="15" aria-hidden="true" />
        <input :value="query" :placeholder="placeholder" :aria-label="searchLabel" @input="$emit('update:query', ($event.target as HTMLInputElement).value)" />
        <button v-if="query" type="button" class="search-clear" aria-label="清空搜索" @click="$emit('update:query', '')"><X :size="14" aria-hidden="true" /></button>
      </label>
      <slot />
    </div>
    <div v-if="modelValue !== options[0] || query.trim()" class="active-filters">
      <span class="filter-caption">当前筛选</span>
      <button v-if="modelValue !== options[0]" class="filter-chip" type="button" :aria-label="'取消分类：' + modelValue" @click="$emit('update:modelValue', options[0])">{{ modelValue }}<X :size="12" aria-hidden="true" /></button>
      <span v-if="query.trim()" class="filter-chip query-chip" :title="query">关键词：{{ query.trim() }}</span>
      <button class="text-action" type="button" @click="reset">清除筛选</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Search, X } from 'lucide-vue-next'
const props = withDefaults(defineProps<{ modelValue: string; query: string; options: string[]; label?: string; placeholder?: string; searchLabel?: string }>(), {
  label: '分类筛选', placeholder: '搜索名称或代码', searchLabel: '搜索名称或代码'
})
const emit = defineEmits<{ 'update:modelValue': [value: string]; 'update:query': [value: string] }>()
function reset() {
  emit('update:modelValue', props.options[0])
  emit('update:query', '')
}
</script>
