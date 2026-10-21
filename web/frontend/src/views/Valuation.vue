<template>
  <LiveValuation v-if="liveBackend" />
  <ValuationPanel v-else :snapshot="snapshot" demo />
</template>
<script setup lang="ts">
import { ref } from 'vue'
import LiveValuation from '../components/LiveValuation.vue'
import ValuationPanel from '../components/ValuationPanel.vue'
import { mockValuation } from '../data/batch2'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
const snapshot = ref(mockValuation())
</script>
