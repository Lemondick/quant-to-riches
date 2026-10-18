import { copyModelConfiguration, isModelConfiguration, normalizeApiBaseUrl, validateModelConfiguration, type ModelConfiguration, type ModelConnections } from '../utils/modelConfig.ts'

export type RemoteModelConfiguration = { configuration: ModelConfiguration; connections: ModelConnections }

// The backend owns provider credentials, discovery protocols and Jev connectivity.
export function createModelConfigApi(baseUrl: string, fetcher: typeof fetch = fetch) {
  const base = baseUrl.trim().replace(/\/+$/, '')
  async function request(path: string, method: string, body?: unknown, signal?: AbortSignal): Promise<unknown> {
    if (!base) throw new Error('后端服务尚未接入，可先保存本地模型配置。')
    const controller = new AbortController()
    const cancel = () => controller.abort()
    signal?.addEventListener('abort', cancel, { once: true })
    if (signal?.aborted) controller.abort()
    const timeout = setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetcher(`${base}/model-config${path}`, {
        method, signal: controller.signal, credentials: 'same-origin', redirect: 'error',
        headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      })
      if (!response.ok) {
        const messages: Record<number, string> = {
          401: '服务鉴权失败，请检查登录状态或 API Key 后重试。',
          403: '当前账号或 Key 没有访问权限，请检查服务授权。',
          404: '未找到模型配置接口，请确认后端服务已接入。',
          429: '请求过于频繁，请稍后重试。',
        }
        throw new Error(messages[response.status] ?? `服务请求失败（HTTP ${response.status}），请稍后重试。`)
      }
      if (response.status === 204) return null
      try { return await response.json() } catch { throw new Error('服务未返回有效的 JSON，请检查后端接口。') }
    } catch (error) {
      if (controller.signal.aborted) throw new Error(signal?.aborted ? '请求已取消。' : '连接超时，请检查后端服务后重试。')
      if (error instanceof TypeError) throw new Error('无法连接后端服务，请检查网络、服务地址和跨域设置。')
      throw error
    } finally {
      clearTimeout(timeout)
      signal?.removeEventListener('abort', cancel)
    }
  }

  function parseConfiguration(value: unknown): RemoteModelConfiguration {
    if (!isModelConfiguration(value)) throw new Error('模型配置格式不正确，请检查后端返回的数据。')
    const configuration = copyModelConfiguration(value)
    if (validateModelConfiguration(configuration)) throw new Error('模型配置包含无效地址或模型 ID，请检查后端返回的数据。')
    if (configuration.baseUrl) configuration.baseUrl = normalizeApiBaseUrl(configuration.baseUrl)
    const connections = (value as ModelConfiguration & { connections?: ModelConnections }).connections
    return { configuration, connections: {
      compatible: connections?.compatible === true, jev: connections?.jev === true,
    } }
  }

  async function connect(path: string, body: unknown, signal?: AbortSignal): Promise<string[]> {
    const result = await request(path, 'POST', body, signal) as { models?: unknown } | null
    if (!result || !Array.isArray(result.models) || !result.models.every(model => typeof model === 'string' && model.trim())) {
      throw new Error('模型列表格式不正确，请检查后端返回的数据。')
    }
    const models = [...new Set((result.models as string[]).map(model => model.trim()))].sort((a, b) => a.localeCompare(b))
    if (!models.length) throw new Error('当前 Key 没有可用模型，请检查服务权限后重试。')
    return models
  }

  return {
    configured: !!base,
    async read(signal?: AbortSignal) { return parseConfiguration(await request('', 'GET', undefined, signal)) },
    async defaults(signal?: AbortSignal) { return parseConfiguration(await request('/defaults', 'GET', undefined, signal)).configuration },
    async save(config: ModelConfiguration, signal?: AbortSignal) { await request('', 'PUT', copyModelConfiguration(config), signal) },
    connectCompatible(baseUrl: string, apiKey: string, signal?: AbortSignal) {
      return connect('/connection', { baseUrl, ...(apiKey ? { apiKey } : {}) }, signal)
    },
    connectJev(apiKey: string, signal?: AbortSignal) {
      return connect('/jev-connection', apiKey ? { apiKey } : {}, signal)
    },
  }
}
