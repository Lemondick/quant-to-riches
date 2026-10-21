<template>
  <LiveResearchFeed v-if="liveBackend" />
  <div v-else>
    <PageHeader title="研报资讯" subtitle="持仓标的的研报与资讯聚合，按发布时间倒序。" />
    <div class="panel card-pad empty-state" role="status">
      <FileSearch :size="30" aria-hidden="true" />
      <h2>演示模式</h2>
      <p>后端未连接，不展示虚构研报内容。</p>
    </div>
  </div>
</template>
<script setup lang="ts">
import { FileSearch } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import LiveResearchFeed from '../components/LiveResearchFeed.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
</script>
