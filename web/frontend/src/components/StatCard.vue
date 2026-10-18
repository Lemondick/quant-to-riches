<template>
  <div class="stat-card" :class="tone">
    <div class="stat-top"><span>{{ label }}<span v-if="updatedAt !== undefined" data-update-time> · {{ updateTime(updatedAt) }}</span></span><span class="mini-icon"><component :is="icon" :size="15" aria-hidden="true" /></span></div>
    <div class="stat-value">{{ value }}</div>
    <div class="stat-foot"><span :class="deltaTone">{{ delta }}</span><span class="muted">{{ note }}</span></div>
    <div class="sparkline" aria-hidden="true"><i v-for="(height, index) in bars" :key="index" :style="{ height: height + '%' }"></i></div>
  </div>
</template>
<script setup lang="ts">
import { updateTime } from '../utils/dataQuality'
import { computed } from 'vue'
import type { Component } from 'vue'
const props = withDefaults(defineProps<{ updatedAt?: string | null; label: string; value: string; delta: string; note: string; icon: Component; positive?: boolean; tone?: string }>(), { tone: '' })
const bars = [18, 25, 20, 33, 27, 44, 39, 58, 48, 65, 59, 74]
const deltaTone = computed(() => props.delta.trim().startsWith('-') ? 'down' : props.delta.trim().startsWith('+') ? 'up' : 'muted')
</script>
