<template>
  <div class="page-context" v-if="metrics"><span class="data-badge">风险测算</span><span>测算时点 {{ updateTime(metrics.computed_at) }} · 区间 {{ metrics.period_days }} 天</span></div>
  <div class="stats-grid" v-if="metrics">
    <StatCard label="VaR（95%）" :value="fmtRiskPct(metrics.var95)" delta="历史模拟法 · 一日" note="未来一日最大可能损失占比" tone="red" :icon="ShieldAlert" />
    <StatCard label="最大回撤" :value="fmtRiskPct(metrics.max_drawdown)" :delta="'区间 ' + metrics.period_days + ' 天'" note="净值峰值到谷底的最大跌幅" tone="red" :icon="TrendingDown" />
    <StatCard label="年化波动率" :value="fmtRiskPct(metrics.ann_volatility)" delta="波动剧烈程度" note="" tone="purple" :icon="Activity" />
    <StatCard label="夏普比率" :value="fmtRiskRatio(metrics.sharpe)" delta="单位波动的超额收益" note="" :icon="Scale" />
    <StatCard label="卡玛比率" :value="fmtRiskRatio(metrics.calmar)" delta="单位回撤的年化收益" note="" :icon="Gauge" />
  </div>
  <div v-else class="panel card-pad empty-state" role="status">
    <FileSearch :size="30" aria-hidden="true" />
    <h2>暂无风险指标</h2>
    <p>{{ emptyReason }}</p>
  </div>

  <div class="tabs-line section-tabs section-space" role="group" aria-label="风险看板">
    <button v-for="item in tabs" :key="item" :aria-pressed="tab === item" :class="{ active: tab === item }" @click="tab = item">{{ item }}</button>
  </div>

  <div v-if="tab === '风险曲线'" class="panel card-pad">
    <div class="detail-title"><h2 class="card-title">净值曲线与回撤</h2><span v-if="metrics" class="tag">区间 {{ metrics.period_days }} 天</span></div>
    <RiskNavChart v-if="drawdownPoints.length" :points="drawdownPoints" />
    <div v-else class="empty-state" role="status">
      <FileSearch :size="30" aria-hidden="true" />
      <h2>暂无净值序列</h2>
      <p>后端风险接口未返回净值序列，曲线无法绘制。{{ emptyReason }}</p>
    </div>
  </div>

  <div v-if="tab === '压力测试'" class="panel card-pad">
    <div class="detail-title"><h2 class="card-title">压力测试</h2><span class="tag blue">假想情景</span></div>
    <p class="disclaimer" role="alert"><AlertTriangle :size="16" aria-hidden="true" />{{ STRESS_DISCLAIMER_FALLBACK }}</p>
    <p v-if="stress?.disclaimer" class="subtle">{{ stress.disclaimer }}</p>
    <div v-if="stressScenarios.length" class="stress-list">
      <div v-for="scenario in stressScenarios" :key="scenario.name" class="stress-row">
        <div class="stress-head"><b>{{ scenario.name }}</b><span class="subtle">宽基指数区间跌幅 {{ scenario.index_drop_pct.toFixed(1) }}%</span></div>
        <div class="stress-bar" role="img" :aria-label="`${scenario.name}：假想损失 ${scenario.est_loss_pct.toFixed(2)}%`">
          <i :style="{ width: stressBarWidth(scenario, stressMax) + '%' }"></i>
        </div>
        <div class="stress-loss">
          <b class="down">−{{ scenario.est_loss_pct.toFixed(2) }}%</b>
          <span class="subtle">{{ scenario.est_loss_amount !== null ? '约 ¥' + Math.round(scenario.est_loss_amount).toLocaleString('zh-CN') : '金额暂无测算' }}</span>
        </div>
      </div>
    </div>
    <div v-else class="empty-state" role="status">
      <FileSearch :size="30" aria-hidden="true" />
      <h2>暂无压力测试</h2>
      <p>{{ emptyReason }}</p>
    </div>
  </div>

  <details class="panel card-pad section-space method-details">
    <summary class="detail-title"><h2 class="card-title">方法论说明</h2><ChevronDown :size="15" aria-hidden="true" /></summary>
    <p class="subtle">{{ riskMethodNote(metrics?.method ?? '') }}</p>
    <p class="subtle">最大回撤、年化波动率、夏普与卡玛比率均基于同一区间的已确认净值序列计算；压力测试的指数区间跌幅为 worker 常量表 STRESS_SCENARIOS 的假想值（2015 股灾 / 2020-03 疫情 / 2022 熊市三档），损失金额按当前持仓市值折算。</p>
  </details>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { ShieldAlert, TrendingDown, Activity, Scale, Gauge, AlertTriangle, ChevronDown, FileSearch } from 'lucide-vue-next'
import StatCard from './StatCard.vue'
import RiskNavChart from './RiskNavChart.vue'
import { updateTime } from '../utils/dataQuality'
import {
  computeDrawdown, fmtRiskPct, fmtRiskRatio, riskMethodNote, maxStressLoss, stressBarWidth,
  STRESS_DISCLAIMER_FALLBACK,
  type RiskMetrics, type StressTestResult,
} from '../services/batch3Api'

const props = defineProps<{ metrics: RiskMetrics | null; stress: StressTestResult | null; demo?: boolean }>()
const tabs = ['风险曲线', '压力测试']
const tab = ref('风险曲线')
const drawdownPoints = computed(() => computeDrawdown(props.metrics?.nav_series ?? []))
const stressScenarios = computed(() => props.stress?.scenarios ?? [])
const stressMax = computed(() => maxStressLoss(stressScenarios.value))
const emptyReason = computed(() =>
  props.demo ? '演示模式：后端未连接，不展示虚构数字。' : '后端风险接口尚未就绪（流 A 并行开发中），等待联调。')
</script>
<style scoped>
.disclaimer{display:flex;gap:8px;align-items:flex-start;padding:10px 12px;border:1px solid #7a2e2e;background:rgba(255,107,107,.1);border-radius:8px;color:#ffb4b4;font-weight:600;margin-bottom:10px}
.stress-list{display:flex;flex-direction:column;gap:14px;margin-top:12px}
.stress-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:6px}
.stress-bar{height:14px;border-radius:7px;background:rgba(255,107,107,.14);overflow:hidden}
.stress-bar i{display:block;height:100%;border-radius:7px;background:linear-gradient(90deg,#ff8f8f,#ff4d4d)}
.stress-loss{display:flex;gap:10px;align-items:baseline;margin-top:6px}
.down{color:#ff7b7b}
.method-details summary{cursor:pointer;list-style:none}
.method-details p{margin-top:8px;line-height:1.7}
</style>
