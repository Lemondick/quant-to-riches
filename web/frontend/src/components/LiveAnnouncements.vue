<template>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!list" @retry="refresh()" />
  <AnnouncementsPanel v-if="list" :list="list" />
  <PageHeader v-else title="公告流" subtitle="持仓基金公告聚合，按发布时间倒序。" />
</template>
<script setup lang="ts">
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import AnnouncementsPanel from './AnnouncementsPanel.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { loadAnnouncements } from '../services/batch2Api'
const { data: list, error, refreshing, refresh } = useAccountResource('announcements', loadAnnouncements)
</script>
