<template>
  <LiveAnnouncements v-if="liveBackend" />
  <AnnouncementsPanel v-else :list="list" demo />
</template>
<script setup lang="ts">
import { ref } from 'vue'
import LiveAnnouncements from '../components/LiveAnnouncements.vue'
import AnnouncementsPanel from '../components/AnnouncementsPanel.vue'
import { mockAnnouncements } from '../data/batch2'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
const list = ref(mockAnnouncements())
</script>
