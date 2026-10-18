export const modelRoles = [
  { id: 'news', label: '资讯整理模型', description: '整理资讯、提取事件与研究要点', provider: 'compatible' },
  { id: 'strategy', label: '候选与策略模型', description: '筛选候选标的、形成策略思路', provider: 'compatible' },
  { id: 'risk', label: '风险复核模型', description: '复核风险、检查研究结论', provider: 'compatible' },
  { id: 'jev', label: 'Jev 判断模型', description: '通过后端 Jev 服务完成判断', provider: 'jev' },
] as const

export type ModelRole = typeof modelRoles[number]['id']
export type ModelProvider = typeof modelRoles[number]['provider']
export type ModelConfiguration = { baseUrl: string; models: Record<ModelRole, string> }
export type ModelConnections = Record<ModelProvider, boolean>

export const defaultModelConfiguration: ModelConfiguration = {
  baseUrl: '',
  models: { news: 'gemini-3.1-pro-low', strategy: 'gpt-6.1-sol', risk: 'gpt-6-astra', jev: 'jev-latest' },
}

export function isModelConfiguration(value: unknown): value is ModelConfiguration {
  if (!value || typeof value !== 'object') return false
  const config = value as ModelConfiguration
  return typeof config.baseUrl === 'string' && !!config.models && typeof config.models === 'object'
    && modelRoles.every(role => typeof config.models[role.id] === 'string')
}

// Whitelist fields so credentials can never enter local preferences or backups.
export function copyModelConfiguration(value: ModelConfiguration): ModelConfiguration {
  return { baseUrl: value.baseUrl.trim(), models: {
    news: value.models.news.trim(), strategy: value.models.strategy.trim(),
    risk: value.models.risk.trim(), jev: value.models.jev.trim(),
  } }
}

export function normalizeApiBaseUrl(value: string): string {
  let url: URL
  try { url = new URL(value.trim()) } catch { throw new Error('请填写有效的兼容 API 地址，例如 https://你的服务/v1。') }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('API 地址应使用 HTTP 或 HTTPS，且不包含账号、密码、查询参数或锚点。')
  }
  return url.href.replace(/\/+$/, '')
}

export function validateModelConfiguration(value: ModelConfiguration): string {
  if (value.baseUrl.trim()) {
    try { normalizeApiBaseUrl(value.baseUrl) } catch (error) { return (error as Error).message }
  }
  for (const role of modelRoles) {
    if (!value.models[role.id].trim()) return `请填写${role.label}。`
    if (value.models[role.id].trim().length > 200) return `${role.label}不能超过 200 个字符。`
  }
  return ''
}
