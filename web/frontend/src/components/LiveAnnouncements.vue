<template>
  <AnnouncementsPanel v-if="list" :list="list" />
  <template v-else>
    <PageHeader title="公告流" subtitle="持仓基金公告聚合，按发布时间倒序。" />
    <BackendState :error="error" :refreshing="refreshing && !loadedOnce" :has-data="loadedOnce" @retry="load()" />
    <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import AnnouncementsPanel from './AnnouncementsPanel.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import type { AnnouncementList } from '../services/batch2Api'

/** 后端接口（流 A 并行开发）：GET api/v1/announcements（worker op announcements.fetch）；404 回退演示数据。 */
const { error, refreshing, session, read } = useAccountResource('announcements',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const list = ref<AnnouncementList | null>(null)
const loadedOnce = ref(false)
const loadError = ref('')

async function load() {
  loadError.value = ''
  try {
    list.value = await read<AnnouncementList>('/announcements')
    loadedOnce.value = true
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      const { mockAnnouncements } = await import('../data/batch2')
      list.value = mockAnnouncements()
      loadedOnce.value = true
      loadError.value = '后端公告接口尚未就绪（A 并行开发中），当前为演示数据。'
    } else {
      loadError.value = failure instanceof Error ? failure.message : '公告读取失败。'
    }
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
