<template>
  <PageHeader title="市场资讯" subtitle="按主题与关联标的整理信息，保留值得继续研究的线索。"><button class="btn" :class="{ primary: onlySaved }" :aria-pressed="onlySaved" @click="onlySaved = !onlySaved"><Bookmark :size="15" />{{ onlySaved ? '查看全部资讯' : '我的收藏' }} · {{ saved.length }}</button><button class="btn" :disabled="refreshing" @click="refresh()"><RefreshCw :size="15" />刷新资讯</button></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <template v-if="data">
    <div class="page-context"><span class="data-badge">公开资讯</span><span>{{ data.stale || stale ? '最近一次有效快照' : '资讯快照已更新' }}</span></div>
    <FilterBar v-model="category" v-model:query="query" :options="['全部', ...sources]" label="资讯分类" search-label="搜索资讯" placeholder="搜索标题、内容或来源" />
    <div class="list-summary" role="status" aria-live="polite">找到 <b>{{ filtered.length }}</b> 条{{ onlySaved ? '收藏' : '' }}资讯</div>
    <div v-if="filtered.length" class="cards-list"><article v-for="item in filtered" :key="item.id" class="panel news-card"><div class="news-meta"><span>{{ item.source }} · {{ item.published_at }}</span><span class="tag blue">市场资讯</span></div><h3>{{ item.title }}</h3><p>{{ item.summary }}</p><div class="news-card-foot"><span class="subtle">{{ item.source }}</span><button class="text-action" :aria-pressed="saved.includes(item.id)" :aria-label="(saved.includes(item.id) ? '取消收藏：' : '收藏：') + item.title" @click="toggleSaved(item.id)"><Bookmark :size="15" :fill="saved.includes(item.id) ? 'currentColor' : 'none'" />{{ saved.includes(item.id) ? '已收藏' : '收藏' }}</button></div></article></div>
    <div v-else class="panel empty-state"><FileSearch :size="32" /><h2>暂无匹配资讯</h2><p>{{ data.unavailable ? '资讯源暂不可用。' : '试试其他分类或关键词。' }}</p><button class="btn" @click="category = '全部'; query = ''; onlySaved = false">重置筛选</button></div>
  </template>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bookmark, RefreshCw, FileSearch } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import FilterBar from './FilterBar.vue'
import BackendState from './BackendState.vue'
import { useApiLoader } from '../composables/useApiLoader'
import { createDataApi } from '../services/dataApi'
type News = { items: { id: string; title: string; summary: string; source: string; published_at: string }[]; stale: boolean; unavailable: boolean }
const base = import.meta.env.VITE_API_BASE_URL ?? '', api = createDataApi(base)
const { data, error, refreshing, stale, refresh } = useApiLoader(base + ':public:news', async signal => { const value = await api.read<News>('/market/news?limit=50', signal); if (!Array.isArray(value.items)) throw new Error('资讯快照格式无效。'); return value }, 60000)
const category = ref('全部'), query = ref(''), onlySaved = ref(false), saved = ref<string[]>([])
const sources = computed(() => [...new Set((data.value?.items ?? []).map(item => item.source))])
const filtered = computed(() => (data.value?.items ?? []).filter(item => (category.value === '全部' || item.source === category.value) && (!onlySaved.value || saved.value.includes(item.id)) && (item.title + item.summary + item.source).includes(query.value.trim())))
function toggleSaved(id: string) { saved.value = saved.value.includes(id) ? saved.value.filter(item => item !== id) : [...saved.value, id] }
</script>
