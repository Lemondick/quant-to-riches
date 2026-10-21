<template>
  <RouterLink to="/risk" class="panel risk-widget" aria-label="前往风险看板">
    <div class="widget-head"><b>组合风险</b><span class="subtle">{{ snapshot.computed_at.slice(0, 10) }}</span></div>
    <div class="widget-body">
      <div class="widget-numbers">
        <div class="widget-number"><span class="subtle">VaR（95%）</span><b class="down">{{ fmtRiskPct(snapshot.var95) }}</b></div>
        <div class="widget-number"><span class="subtle">最大回撤</span><b class="down">{{ fmtRiskPct(snapshot.max_drawdown) }}</b></div>
      </div>
      <div class="widget-extra">
        <span class="subtle">年化波动 {{ fmtRiskPct(snapshot.ann_volatility) }}</span>
        <span class="subtle">夏普 {{ fmtRiskRatio(snapshot.sharpe) }}</span>
      </div>
    </div>
  </RouterLink>
</template>
<script setup lang="ts">
import { fmtRiskPct, fmtRiskRatio, type RiskMetrics } from '../services/batch3Api'
defineProps<{ snapshot: RiskMetrics }>()
</script>
<style scoped>
.risk-widget{display:block;text-decoration:none;color:inherit;padding:14px 16px;margin-top:12px;transition:transform .25s}
.risk-widget:hover{transform:translateY(-2px)}
.widget-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.subtle{font-size:11px;color:var(--text-2)}
.widget-body{display:flex;flex-direction:column;gap:8px}
.widget-numbers{display:flex;gap:28px}
.widget-number{display:flex;flex-direction:column;gap:4px}
.widget-number b{font-size:20px;font-variant-numeric:tabular-nums}
.widget-extra{display:flex;gap:18px}
.down{color:#ff7b7b}
</style>
