import { ref } from 'vue'
import { downloadFile } from '../utils/format'

/**
 * F11 整账户一键导出：前端聚合已有只读接口，打包为单个 JSON 下载。
 * 后端暂无统一的整账户导出端点（属 A 的后端地盘，不新增），故用前端聚合实现；
 * 每个分区独立 try/catch，单个接口失败不影响其他分区。
 */
const EXPORT_TARGETS: [section: string, path: string][] = [
  ['dashboard_summary', '/dashboard/summary'],
  ['ledger', '/ledger?limit=500'],
  ['history_ledger', '/history/ledger/export'],
  ['history_actions', '/history/actions/export'],
  ['history_corrections', '/history/corrections/export'],
  ['preferences_watchlist', '/preferences'],
  ['advisor_reports', '/advisor/reports?limit=100'],
  ['attribution_reports', '/attribution/reports?limit=100'],
  ['alerts', '/alerts'],
]

export function useAccountExport(read: (path: string) => Promise<unknown>, username: () => string) {
  const exporting = ref(false), progress = ref(''), error = ref(''), notice = ref('')

  async function exportAll() {
    exporting.value = true
    error.value = ''
    notice.value = ''
    const sections: Record<string, { ok: boolean; data?: unknown; error?: string }> = {}
    try {
      for (const [name, path] of EXPORT_TARGETS) {
        progress.value = name
        try {
          sections[name] = { ok: true, data: await read(path) }
        } catch (failure) {
          sections[name] = { ok: false, error: failure instanceof Error ? failure.message : String(failure) }
        }
      }
      const user = username()
      const payload = {
        exported_at: new Date().toISOString(),
        exported_by: user,
        source: 'frontend-aggregation',
        note: '后端暂无统一整账户导出接口；本文件由前端聚合已有只读接口生成，各分区独立获取、互不影响。',
        sections,
      }
      downloadFile(`观澜-整账户导出-${user}-${payload.exported_at.slice(0, 10)}.json`, JSON.stringify(payload, null, 2), 'application/json')
      const failed = Object.entries(sections).filter(([, section]) => !section.ok).map(([name]) => name)
      notice.value = failed.length ? `导出完成，部分分区获取失败：${failed.join('、')}（已在文件中标注）。` : '整账户数据已导出为 JSON 文件。'
    } catch (failure) {
      error.value = failure instanceof Error ? failure.message : '导出失败。'
    } finally {
      exporting.value = false
      progress.value = ''
    }
  }

  return { exporting, progress, error, notice, exportAll }
}
