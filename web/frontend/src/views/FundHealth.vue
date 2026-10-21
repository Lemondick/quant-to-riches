<template>
  <LiveFundHealth v-if="liveBackend" />
  <FundHealthPanel v-else :snapshot="snapshot" demo />
</template>
<script setup lang="ts">
import { ref } from 'vue'
import LiveFundHealth from '../components/LiveFundHealth.vue'
import FundHealthPanel from '../components/FundHealthPanel.vue'
import { mockFundHealth } from '../data/batch2'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
const snapshot = ref(mockFundHealth())
</script>
