<template>
  <PageHeader title="研报资讯" subtitle="持仓标的的研报与资讯聚合，按发布时间倒序。">
    <button class="btn primary" :disabled="refreshingItems" @click="refreshItems()"><RefreshCw :size="15" aria-hidden="true" />{{ refreshingItems ? '刷新中…' : '手动刷新' }}</button>
  </PageHeader>
  <BackendState :error="error" :refreshing="refreshing && !list" :has-data="!!list" @retry="load()" />
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <p v-if="loadError" class="feedback error" role="alert">{{ loadError }}</p>
  <template v-if="list">
    <div class="page-context"><span class="data-badge">公开研报</span><span>{{ list.fetched_at ? '上次抓取 ' + updateTime(list.fetched_at) : '抓取时间未知' }}</span></div>
    <FilterBar v-model="kind" v-model:query="query" :options="['全部', '研报', '资讯']" label="内容类型" placeholder="搜索标题、摘要或来源" search-label="搜索研报资讯">
      <label class="symbol-filter">标的
        <select v-model="symbol" class="field" aria-label="按标的筛选">
          <option value="">全部标的</option>
          <option v-for="item in symbols" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
    </FilterBar>
    <div class="list-summary" role="status" aria-live="polite">找到 <b>{{ filtered.length }}</b> 条</div>
    <div v-if="filtered.length" class="cards-list">
      <article v-for="item in filtered" :key="researchItemId(item)" class="panel news-card">
        <div class="news-meta">
          <span>{{ item.source }} · {{ updateTime(item.published_at) }}</span>
          <span class="tag blue">{{ item.kind === 'report' ? '研报' : '资讯' }}</span>
        </div>
        <h3>{{ item.title }}</h3>
        <p>{{ item.summary || '暂无摘要。' }}</p>
        <div class="news-card-foot">
          <span class="subtle">{{ item.symbol || '综合' }}</span>
          <a v-if="item.url" class="text-action" :href="item.url" target="_blank" rel="noopener noreferrer">查看原文 <ArrowUpRight :size="13" aria-hidden="true" /></a>
        </div>
      </article>
    </div>
    <div v-else class="panel empty-state" role="status">
      <FileSearch :size="32" aria-hidden="true" />
      <h2>暂无匹配研报</h2>
      <p>{{ list.items.length ? '试试其他类型、标的或关键词。' : '研报源暂未接入或暂无抓取结果，不展示虚构内容。点击「手动刷新」可触发抓取。' }}</p>
      <button class="btn" @click="kind = '全部'; symbol = ''; query = ''">重置筛选</button>
    </div>
  </template>
  <div v-else-if="!refreshing && !error" class="panel card-pad empty-state" role="status">
    <FileSearch :size="30" aria-hidden="true" />
    <h2>研报数据暂未就绪</h2>
    <p>后端研报接口（api/v1/research，流 A 并行开发中）尚未可用，暂无数据可展示，不展示虚构内容。</p>
    <button class="btn" :disabled="refreshing" @click="load()">重试读取</button>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowUpRight, FileSearch, RefreshCw } from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import FilterBar from './FilterBar.vue'
import BackendState from './BackendState.vue'
import { ApiError } from '../services/dataApi'
import { updateTime } from '../utils/dataQuality'
import { useAccountResource } from '../composables/useAccountResource'
import {
  filterResearchItems, researchItemId, researchSymbols, sortResearchDesc,
  type ResearchKind, type ResearchList,
} from '../services/batch3Api'

/** 后端接口（流 A 并行开发）：GET api/v1/research · POST api/v1/research/refresh；
 *  未就绪时展示空态+说明（铁律：不虚构研报内容）。 */
const { error, refreshing, session, read, mutate } = useAccountResource('research',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const list = ref<ResearchList | null>(null)
const loadError = ref('')
const notice = ref('')
const refreshingItems = ref(false)
const kind = ref('全部')
const symbol = ref('')
const query = ref('')

const symbols = computed(() => researchSymbols(list.value?.items ?? []))
const filtered = computed(() => sortResearchDesc(filterResearchItems(
  list.value?.items ?? [],
  symbol.value,
  kind.value === '全部' ? 'all' : (kind.value === '研报' ? 'report' : 'news') as ResearchKind,
)).filter(item => {
  const keyword = query.value.trim()
  return !keyword || (item.title + (item.summary ?? '') + item.source + (item.symbol ?? '')).includes(keyword)
}))

async function load() {
  loadError.value = ''
  notice.value = ''
  try {
    list.value = await read<ResearchList>('/research')
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 404) {
      list.value = null
    } else {
      loadError.value = failure instanceof Error ? failure.message : '研报读取失败。'
    }
  }
}

/** 手动刷新：POST /research/refresh 触发抓取，成功后重新拉取列表；带 loading 态。 */
async function refreshItems() {
  refreshingItems.value = true
  loadError.value = ''
  notice.value = ''
  try {
    await mutate<unknown>('/research/refresh', { symbols: [], kinds: ['report', 'news'] })
    await load()
    notice.value = '刷新请求已提交，列表已更新。'
  } catch (failure) {
    loadError.value = failure instanceof Error ? failure.message : '刷新失败。'
  } finally {
    refreshingItems.value = false
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
<style scoped>
.symbol-filter{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--text-2)}
.symbol-filter .field{min-width:160px}
</style>
