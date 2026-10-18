<template>
  <LiveFundReview v-if="liveBackend" />
  <template v-else>
  <PageHeader title="基金复核" subtitle="把表现、关注状态与研究备注放在同一处。"><button class="btn" :disabled="!rows.length" @click="exportReview"><Download :size="15" aria-hidden="true" />导出复核</button><button class="btn primary" @click="addOpen = true"><Plus :size="15" aria-hidden="true" />添加基金</button></PageHeader>
  <div class="page-context"><span class="data-badge">演示复核</span><span>收益与评级为样本数据，复核状态和备注保存在本浏览器。</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <div class="kpi-grid">
    <div class="panel metric-card"><span class="metric-label">复核清单</span><div class="metric-value">{{ queue.codes.length }} 只</div><div class="metric-note">已添加的基金样本</div></div>
    <div class="panel metric-card"><span class="metric-label">待复核</span><div class="metric-value">{{ queue.codes.filter(code => !queue.reviewed.includes(code)).length }} 只</div><div class="metric-note">完成后可标记并保留备注</div></div>
    <div class="panel metric-card"><span class="metric-label">已复核</span><div class="metric-value up">{{ queue.reviewed.filter(code => queue.codes.includes(code)).length }} 只</div><div class="metric-note">支持重新打开复核</div></div>
  </div>
  <div class="panel table-panel section-space">
    <div class="panel-head"><div><h2>基金复核清单</h2><p>点击基金名称，查看下方走势与研究记录。</p></div></div>
    <FilterBar v-model="status" v-model:query="query" :options="['全部', '待复核', '已复核', '已关注']" class="panel-filters" label="复核状态" placeholder="搜索基金名称或代码" search-label="搜索复核基金" />
    <DataTable table-id="fund-review" label="基金复核清单" :headers="['基金', '代码', '净值', '日涨跌', '近1周', '近1月', '复核状态', '操作']" :rows="rows" :numeric-columns="[2, 3, 4, 5]" :min-width="960">
      <template #cell-0="{ value, row }"><button class="table-name-button" :class="{ selected: selectedCode === row[1] }" :aria-label="'查看复核：' + value" @click="selectFund(String(row[1]))">{{ value }}</button></template>
      <template #cell-6="{ value }"><span class="tag" :class="{ blue: value === '待复核' }">{{ value }}</span></template>
      <template #cell-7="{ row }"><button class="text-action" :aria-label="'切换复核状态：' + row[0]" @click="toggleReviewed(String(row[1]))">{{ queue.reviewed.includes(String(row[1])) ? '重新复核' : '标记已复核' }}</button></template>
      <template #empty-action><button class="btn" @click="status = '全部'; query = ''">清除筛选</button></template>
    </DataTable>
  </div>
  <div v-if="selectedFund" ref="details" class="two-col section-space">
    <ChartCard :title="selectedFund.name" subtitle="选中基金与同类样本的演示表现" :legends="legends" />
    <div class="panel card-pad"><div class="detail-title"><div><h2 class="card-title">复核记录</h2><p class="subtle">{{ selectedFund.code }} · {{ selectedFund.type }}</p></div><span class="tag" :class="{ blue: !queue.reviewed.includes(selectedCode) }">{{ queue.reviewed.includes(selectedCode) ? '已复核' : '待复核' }}</span></div>
      <div class="row-list"><div class="row"><span>最新净值</span><b>{{ selectedFund.nav }}</b></div><div class="row"><span>近1月样本收益</span><b :class="parseFloat(selectedFund.m1) >= 0 ? 'up' : 'down'">{{ selectedFund.m1 }}</b></div><div class="row"><span>样本经理</span><b>{{ selectedFund.manager }}</b></div></div>
      <label class="stack-label section-space">研究备注<textarea v-model="note" class="field textarea" rows="4" maxlength="1000" placeholder="记录观察依据、数据口径和下次需要核实的问题。"></textarea></label>
      <div class="inline-actions section-space"><button class="btn" @click="saveNote">保存备注</button><button class="btn primary" @click="toggleReviewed(selectedCode)">{{ queue.reviewed.includes(selectedCode) ? '重新复核' : '完成复核' }}</button></div>
    </div>
  </div>
  <AppDialog v-model="addOpen" title="添加基金到复核清单" description="从演示基金目录选择，已添加的基金不会重复录入。">
    <label class="stack-label">查找基金<input v-model="catalogQuery" class="field" placeholder="输入名称或代码" /></label>
    <div class="selection-list section-space"><label v-for="fund in availableFunds" :key="fund.code" class="selection-item"><input v-model="addCode" type="radio" :value="fund.code" name="review-fund" /><span><b>{{ fund.name }}</b><small>{{ fund.code }} · {{ fund.type }}</small></span></label><p v-if="!availableFunds.length" class="subtle">没有可添加的匹配基金。</p></div>
    <template #footer><button class="btn" @click="addOpen = false">取消</button><button class="btn primary" :disabled="!availableFunds.some(fund => fund.code === addCode)" @click="addFund">添加到清单</button></template>
  </AppDialog>
</template>
</template>
<script setup lang="ts">
import LiveFundReview from '../components/LiveFundReview.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import { computed, ref, watch } from 'vue'
import { Download, Plus } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import DataTable from '../components/DataTable.vue'
import ChartCard from '../components/ChartCard.vue'
import FilterBar from '../components/FilterBar.vue'
import AppDialog from '../components/AppDialog.vue'
import { useLocalState } from '../composables/useLocalState'
import { useFollowedFunds } from '../composables/useFollowedFunds'
import { reviewCatalog } from '../data/research'
import { downloadCsv, percent } from '../utils/format'
type ReviewQueue = { codes: string[]; reviewed: string[]; notes: Record<string, string> }
const queue = useLocalState<ReviewQueue>('guanlan.review.v1', { codes: ['015042', '003096', '510300'], reviewed: [], notes: {} }, (value): value is ReviewQueue => !!value && typeof value === 'object' && Array.isArray((value as ReviewQueue).codes) && (value as ReviewQueue).codes.every(code => reviewCatalog.some(fund => fund.code === code)) && Array.isArray((value as ReviewQueue).reviewed) && (value as ReviewQueue).reviewed.every(code => typeof code === 'string') && !!(value as ReviewQueue).notes && typeof (value as ReviewQueue).notes === 'object' && Object.values((value as ReviewQueue).notes).every(note => typeof note === 'string'))
const { isSaved } = useFollowedFunds()
const status = ref('全部'), query = ref(''), selectedCode = ref(queue.value.codes[0] ?? ''), note = ref(''), notice = ref(''), details = ref<HTMLElement>()
const filtered = computed(() => reviewCatalog.filter(fund => queue.value.codes.includes(fund.code) && (status.value === '全部' || status.value === '已关注' && isSaved(fund.code) || status.value === '已复核' && queue.value.reviewed.includes(fund.code) || status.value === '待复核' && !queue.value.reviewed.includes(fund.code)) && (!query.value.trim() || fund.name.includes(query.value.trim()) || fund.code.includes(query.value.trim()))))
const rows = computed(() => filtered.value.map(fund => [fund.name, fund.code, fund.nav, fund.dayChange, fund.w1, fund.m1, queue.value.reviewed.includes(fund.code) ? '已复核' : '待复核', fund.code]))
const selectedFund = computed(() => reviewCatalog.find(fund => fund.code === selectedCode.value))
const legends = computed(() => {
  if (!selectedFund.value) return []
  const peers = reviewCatalog.filter(fund => fund.type === selectedFund.value!.type)
  const average = peers.reduce((sum, fund) => sum + parseFloat(fund.m3), 0) / peers.length
  return [{ name: '基金样本', value: selectedFund.value.m3, color: '#4d8dff' }, { name: '同类样本', value: percent(average), color: '#ff9d54' }]
})
watch(selectedCode, code => { note.value = queue.value.notes[code] ?? '' }, { immediate: true })
function selectFund(code: string) { selectedCode.value = code; details.value?.scrollIntoView({ behavior: 'auto', block: 'start' }) }
function saveNote() { if (selectedCode.value) { queue.value.notes[selectedCode.value] = note.value; notice.value = '研究备注已保存。' } }
function toggleReviewed(code: string) { queue.value.reviewed = queue.value.reviewed.includes(code) ? queue.value.reviewed.filter(item => item !== code) : [...queue.value.reviewed, code]; notice.value = '复核状态已更新。' }
function exportReview() { downloadCsv('观澜-基金复核.csv', ['基金', '代码', '净值', '日涨跌', '近1周', '近1月', '复核状态', '研究备注'], rows.value.map(row => [...row.slice(0, 7), queue.value.notes[String(row[1])] ?? ''])) }
const addOpen = ref(false), catalogQuery = ref(''), addCode = ref('')
const availableFunds = computed(() => reviewCatalog.filter(fund => !queue.value.codes.includes(fund.code) && (!catalogQuery.value.trim() || fund.name.includes(catalogQuery.value.trim()) || fund.code.includes(catalogQuery.value.trim()))))
function addFund() { if (!availableFunds.value.some(fund => fund.code === addCode.value)) return; queue.value.codes.push(addCode.value); selectedCode.value = addCode.value; addOpen.value = false; notice.value = '基金已加入复核清单。' }
</script>
