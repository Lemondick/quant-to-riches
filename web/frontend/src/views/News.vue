<template>
  <LiveNews v-if="liveBackend" />
  <template v-else>
  <PageHeader title="市场资讯" subtitle="按主题与关联标的整理信息，保留值得继续研究的线索。"><button class="btn" :class="{ primary: onlySaved }" :aria-pressed="onlySaved" @click="onlySaved = !onlySaved"><Bookmark :size="15" aria-hidden="true" />{{ onlySaved ? '查看全部资讯' : '我的收藏' }} · {{ saved.length }}</button><button class="btn" @click="refresh"><RefreshCw :size="15" aria-hidden="true" />刷新样本</button></PageHeader>
  <div class="page-context"><span class="data-badge">演示资讯</span><span>文章均为本地示例内容，未连接实时新闻源 · 最近查看 {{ dateTime(checkedAt) }}</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <FilterBar v-model="category" v-model:query="query" :options="['全部', '与我相关', '宏观', '政策', '行业', '公司']" label="资讯分类" search-label="搜索资讯" placeholder="搜索标题、内容或关联代码" />
  <div class="list-summary" role="status" aria-live="polite">找到 <b>{{ filtered.length }}</b> 条{{ onlySaved ? '收藏' : '' }}资讯<span>已读 {{ read.length }} 条</span></div>
  <div v-if="filtered.length" class="cards-list">
    <article v-for="article in filtered" :key="article.id" class="panel news-card" :class="{ 'is-read': read.includes(article.id) }">
      <div class="news-meta"><span>{{ article.type }} · {{ article.time }}</span><span class="tag blue">{{ related(article.codes) ? '与我相关' : article.tag }}</span></div>
      <h3><button class="article-title" @click="openArticle(article.id)">{{ article.title }}</button></h3><p>{{ article.desc }}</p>
      <div class="news-card-foot"><span class="subtle">{{ read.includes(article.id) ? '已读' : '未读' }} · 本地演示资讯</span><button class="text-action" :aria-label="(saved.includes(article.id) ? '取消收藏：' : '收藏：') + article.title" :aria-pressed="saved.includes(article.id)" @click="toggleSaved(article.id)"><Bookmark :size="15" :fill="saved.includes(article.id) ? 'currentColor' : 'none'" aria-hidden="true" />{{ saved.includes(article.id) ? '已收藏' : '收藏' }}</button></div>
    </article>
  </div>
  <div v-else class="panel empty-state"><FileSearch :size="32" aria-hidden="true" /><h2>暂无匹配资讯</h2><p>试试其他分类或关键词，也可以查看全部示例。</p><button class="btn" @click="category = '全部'; query = ''; onlySaved = false">重置筛选</button></div>
  <AppDialog v-model="detailOpen" :title="current?.title ?? '资讯详情'" drawer description="本地演示内容，不代表当前市场事实。">
    <template v-if="current"><div class="article-meta">{{ current.type }} · {{ current.time }} · 来源：本地演示资讯</div><p class="article-lead">{{ current.desc }}</p><p v-for="paragraph in current.body" :key="paragraph" class="article-paragraph">{{ paragraph }}</p><div class="section-space"><h3 class="card-title">关联标的</h3><div class="inline-actions section-space"><span v-for="code in current.codes" :key="code" class="tag blue">{{ code }}</span></div><RouterLink class="text-action section-space" to="/watchlist" @click="detailOpen = false">查看我的自选 <ArrowUpRight :size="13" aria-hidden="true" /></RouterLink></div></template>
    <template #footer><button class="btn" @click="detailOpen = false">关闭</button><button v-if="current" class="btn primary" @click="toggleSaved(current.id)">{{ saved.includes(current.id) ? '取消收藏' : '收藏文章' }}</button></template>
  </AppDialog>
  </template>
</template>
<script setup lang="ts">
import LiveNews from '../components/LiveNews.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()

import { computed, ref } from 'vue'
import { ArrowUpRight, Bookmark, FileSearch, RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import FilterBar from '../components/FilterBar.vue'
import AppDialog from '../components/AppDialog.vue'
import { newsSamples } from '../data/research'
import { useLocalState } from '../composables/useLocalState'
import { usePortfolio } from '../composables/usePortfolio'
import { useFollowedFunds } from '../composables/useFollowedFunds'
import { dateTime } from '../utils/format'
const validIds = (value: unknown): value is string[] => Array.isArray(value) && value.every(id => typeof id === 'string' && newsSamples.some(article => article.id === id))
const saved = useLocalState<string[]>('guanlan.news-saved.v1', [], validIds)
const read = useLocalState<string[]>('guanlan.news-read.v1', [], validIds)
const { portfolio } = usePortfolio()
const { savedFunds } = useFollowedFunds()
const category = ref('全部'), query = ref(''), onlySaved = ref(false), checkedAt = ref(Date.now()), notice = ref(''), detailOpen = ref(false), currentId = ref('')
const related = (codes: string[]) => codes.some(code => savedFunds.value.includes(code) || portfolio.value.holdings.some(holding => holding.code === code))
const filtered = computed(() => newsSamples.filter(article => (!onlySaved.value || saved.value.includes(article.id)) && (category.value === '全部' || category.value === '与我相关' && related(article.codes) || article.type === category.value) && (!query.value.trim() || [article.title, article.desc, ...article.codes].some(value => value.includes(query.value.trim())))))
const current = computed(() => newsSamples.find(article => article.id === currentId.value))
function toggleSaved(id: string) { saved.value = saved.value.includes(id) ? saved.value.filter(value => value !== id) : [...saved.value, id] }
function openArticle(id: string) { currentId.value = id; detailOpen.value = true; if (!read.value.includes(id)) read.value.push(id) }
function refresh() { checkedAt.value = Date.now(); notice.value = '已重新载入本地资讯样本，收藏与已读状态保持不变。' }
</script>
