<template>
  <LiveRebalance v-if="liveBackend" />
  <RebalancePanel v-else :state="state" demo />
</template>
<script setup lang="ts">
import { ref } from 'vue'
import LiveRebalance from '../components/LiveRebalance.vue'
import RebalancePanel from '../components/RebalancePanel.vue'
import { mockRebalanceState } from '../data/batch2'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
const state = ref(mockRebalanceState())
</script>
