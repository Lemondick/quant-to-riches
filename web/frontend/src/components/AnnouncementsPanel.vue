<template>
  <PageHeader title="公告流" subtitle="持仓基金公告聚合，按发布时间倒序。" />
  <div class="page-context"><span class="data-badge">{{ demo ? '演示数据' : '公告聚合' }}</span><span>已读状态仅保存在本机浏览器。</span></div>
  <div class="announce-toolbar">
    <div class="segmented" role="group" aria-label="公告类型筛选">
      <button v-for="option in kindOptions" :key="option.value" :class="{ selected: kindFilter === option.value }" :aria-pressed="kindFilter === option.value" @click="kindFilter = option.value">{{ option.label }}</button>
    </div>
    <div class="toolbar-right">
      <span class="subtle" role="status">{{ unreadCount }} 条未读</span>
      <button class="text-action" @click="markAllRead">全部标为已读</button>
    </div>
  </div>
  <div v-for="item in filtered" :key="announcementId(item)" class="panel card-pad announce-card" :class="{ 'is-risk': item.kind === 'liquidation_risk', 'is-read': readIds.has(announcementId(item)) }">
    <div class="announce-head">
      <span class="tag" :class="kindTagClass(item.kind)">{{ ANNOUNCEMENT_KIND_NAMES[item.kind] }}</span>
      <span v-if="!readIds.has(announcementId(item))" class="unread-dot" aria-label="未读"></span>
      <span class="subtle">{{ item.fund_name ?? item.fund_code }} · {{ item.fund_code }}</span>
      <time class="announce-time">{{ formatTime(item.published_at) }}</time>
    </div>
    <component :is="item.source_url ? 'a' : 'div'" v-bind="item.source_url ? { href: item.source_url, target: '_blank', rel: 'noopener' } : {}" class="announce-title" @click="markRead(item)">
      {{ item.title }}
    </component>
    <p v-if="item.summary" class="announce-summary">{{ item.summary }}</p>
  </div>
  <p v-if="!filtered.length" class="feedback" role="status">暂无此类公告。</p>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import PageHeader from './PageHeader.vue'
import {
  ANNOUNCEMENT_KIND_NAMES, announcementId, loadReadAnnouncementIds, saveReadAnnouncementIds,
  sortAnnouncementsDesc, type Announcement, type AnnouncementKind, type AnnouncementList,
} from '../services/batch2Api'

const props = withDefaults(defineProps<{ list: AnnouncementList; demo?: boolean }>(), { demo: false })

const kindFilter = ref<'all' | AnnouncementKind>('all')
const kindOptions: { value: 'all' | AnnouncementKind; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'dividend', label: '分红' },
  { value: 'manager_change', label: '经理变更' },
  { value: 'liquidation_risk', label: '清盘预警' },
  { value: 'other', label: '其他' },
]
const readIds = ref<Set<string>>(loadReadAnnouncementIds())

const sorted = computed(() => sortAnnouncementsDesc(props.list.announcements))
const filtered = computed(() => kindFilter.value === 'all' ? sorted.value : sorted.value.filter(item => item.kind === kindFilter.value))
const unreadCount = computed(() => sorted.value.filter(item => !readIds.value.has(announcementId(item))).length)

function kindTagClass(kind: AnnouncementKind): string {
  return kind === 'liquidation_risk' ? 'red' : kind === 'dividend' ? 'blue' : kind === 'manager_change' ? 'warn' : ''
}
function markRead(item: Announcement) {
  const id = announcementId(item)
  if (readIds.value.has(id)) return
  readIds.value = new Set(readIds.value).add(id)
  saveReadAnnouncementIds(readIds.value)
}
function markAllRead() {
  readIds.value = new Set(sorted.value.map(announcementId))
  saveReadAnnouncementIds(readIds.value)
}
function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}
</script>
<style scoped>
.announce-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap}
.toolbar-right{display:flex;align-items:center;gap:10px}
.subtle{font-size:11px;color:var(--text-2)}
.announce-card{margin-bottom:10px;transition:opacity .2s}
.announce-card.is-risk{border-color:rgba(224,93,93,.65);background:linear-gradient(145deg,rgba(68,38,53,.75),rgba(13,27,46,.96))}
.announce-card.is-read{opacity:.72}
.announce-head{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}
.tag.warn{background:rgba(68,54,38,.7);color:#ffb86b}
.unread-dot{width:8px;height:8px;border-radius:50%;background:#4d8dff;box-shadow:0 0 8px rgba(77,141,255,.8)}
.announce-time{margin-left:auto;font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
.announce-title{font-size:14px;font-weight:600;color:var(--text-1);text-decoration:none;display:block;margin:2px 0 6px}
a.announce-title:hover{color:#6ca0ff}
.announce-summary{font-size:12px;color:var(--text-2);margin:0;line-height:1.6}
</style>
