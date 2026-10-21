<template>
  <PageHeader title="基金体检" subtitle="按基金分组展示规模、经理、风格、费率四类发现。" />
  <div class="page-context"><span class="data-badge">{{ demo ? '演示数据' : '定期体检' }}</span><span>截至 {{ snapshot.as_of }} · 每周一 06:00 自动更新（QUANT_HEALTH_CHECK_CRON）。</span></div>
  <div class="summary-strip" role="status">
    <span>共 <b>{{ snapshot.reports.length }}</b> 只基金</span>
    <span class="tag red">{{ criticalCount }} 严重</span>
    <span class="tag warn">{{ warnCount }} 关注</span>
    <span class="tag">{{ insufficientCount }} 数据不足</span>
  </div>
  <div v-for="report in snapshot.reports" :key="report.code" class="panel card-pad fund-block">
    <div class="fund-head">
      <div><b class="fund-name">{{ report.name ?? report.code }}</b><span class="mono">{{ report.code }}</span></div>
      <span class="tag" :class="severityClass(worstSeverity(report))">{{ HEALTH_SEVERITY_NAMES[worstSeverity(report)] }}</span>
    </div>
    <div class="finding-grid">
      <div v-for="finding in orderFindings(report)" :key="finding.kind" class="finding" :class="'sev-' + finding.severity">
        <div class="finding-head">
          <b>{{ HEALTH_KIND_NAMES[finding.kind] }}</b>
          <span class="tag" :class="severityClass(finding.severity)">{{ HEALTH_SEVERITY_NAMES[finding.severity] }}</span>
          <span v-if="isInsufficient(finding)" class="tag gray">数据不足</span>
        </div>
        <p>{{ finding.detail }}</p>
        <p v-if="finding.evidence" class="evidence">依据：{{ finding.evidence }}</p>
      </div>
    </div>
  </div>
  <p v-if="!snapshot.reports.length" class="feedback" role="status">暂无体检报告。</p>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import PageHeader from './PageHeader.vue'
import {
  HEALTH_KIND_NAMES, HEALTH_SEVERITY_NAMES, isInsufficient, orderFindings, worstSeverity,
  type FundHealthSnapshot, type HealthSeverity,
} from '../services/batch2Api'

const props = withDefaults(defineProps<{ snapshot: FundHealthSnapshot; demo?: boolean }>(), { demo: false })

function severityClass(severity: HealthSeverity): string {
  return severity === 'critical' ? 'red' : severity === 'warn' ? 'warn' : ''
}
const criticalCount = computed(() => props.snapshot.reports.reduce((n, r) => n + r.findings.filter(f => f.severity === 'critical').length, 0))
const warnCount = computed(() => props.snapshot.reports.reduce((n, r) => n + r.findings.filter(f => f.severity === 'warn').length, 0))
const insufficientCount = computed(() => props.snapshot.reports.reduce((n, r) => n + r.findings.filter(isInsufficient).length, 0))
</script>
<style scoped>
.summary-strip{display:flex;gap:10px;align-items:center;margin:12px 0;font-size:12px;color:var(--text-2)}
.tag.warn{background:rgba(68,54,38,.7);color:#ffb86b}
.tag.gray{background:rgba(52,64,82,.7);color:#9fb0c7}
.fund-block{margin-bottom:12px}
.fund-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.fund-name{font-size:15px;margin-right:8px}
.mono{font-size:11px;color:var(--text-2)}
.finding-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
.finding{border:1px solid var(--border);border-left-width:4px;border-radius:8px;padding:10px 12px;background:rgba(13,24,40,.5)}
.finding.sev-info{border-left-color:#8b98ab}
.finding.sev-warn{border-left-color:#e8a33d}
.finding.sev-critical{border-left-color:#e05d5d;background:rgba(68,38,53,.35)}
.finding-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.finding p{font-size:12px;margin:0 0 4px;color:var(--text-1)}
.finding .evidence{font-size:11px;color:var(--text-2)}
</style>
