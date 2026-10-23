import { computed, onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { ApiError, createDataApi, type BackendSession } from '../services/dataApi'

export type ComputeTask = {
  job_id: string; kind: string; status: 'queued' | 'running' | 'cancel_requested' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  created_at: string; deadline: string; completed_at: string | null; result: unknown; error_code: string | null
}
export type ComputeRequest = { path: string; body?: unknown; method?: 'GET' | 'POST' }
const activeStates = new Set(['queued', 'running', 'cancel_requested'])
const terminalStates = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const api = createDataApi(import.meta.env.VITE_API_BASE_URL ?? '')

/** Only a task ID is persisted; every read/write still verifies the current session scope. */
export function useComputeTask<T>(kind: string, session: Readonly<Ref<BackendSession | undefined>>, normalize: (value: unknown) => T, autoRequest?: ComputeRequest) {
  const task = shallowRef<ComputeTask | null>(null), result = shallowRef<T | null>(null)
  const error = shallowRef<unknown>(), notice = ref(''), taskId = ref<string | null>(null)
  const submitting = ref(false), checking = ref(false), cancelling = ref(false)
  const requesting = computed(() => submitting.value || checking.value || cancelling.value)
  const busy = computed(() => requesting.value || !!task.value && activeStates.has(task.value.status))
  let key: string | null = null, serial = 0, controller: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let preserveResult = false

  function stop() {
    serial++
    controller?.abort(); controller = undefined
    clearTimeout(timer); timer = undefined
    submitting.value = false; checking.value = false; cancelling.value = false
  }
  function persist(id: string | null) {
    if (!key) return
    try {
      if (id) sessionStorage.setItem(key, id)
      else sessionStorage.removeItem(key)
    } catch { notice.value = '当前浏览器无法保存任务，刷新后无法自动恢复。' }
  }
  function accept(value: unknown, legacyAllowed: boolean) {
    if (value && typeof value === 'object' && 'job_id' in value) {
      const next = value as ComputeTask
      if (typeof next.job_id !== 'string' || !/^[\w-]{1,64}$/.test(next.job_id) || next.kind !== kind
        || !activeStates.has(next.status) && !terminalStates.has(next.status)) throw new Error('计算任务数据格式不正确。')
      if (!legacyAllowed && next.job_id !== taskId.value) throw new Error('计算任务标识不一致。')
      task.value = next; taskId.value = next.job_id; persist(next.job_id)
      if (next.status === 'completed') { result.value = normalize(next.result); preserveResult = false }
      else {
        if (!activeStates.has(next.status) || !preserveResult) result.value = null
        if (!activeStates.has(next.status)) preserveResult = false
        if (next.status === 'failed') error.value = new Error(next.error_code === 'invalid_request' ? '计算参数或证据无效，请核对后重新运行。' : '计算失败，请检查数据和计算服务后重新运行。')
        if (next.status === 'interrupted') error.value = new Error('任务已超时或中断，请重新运行。')
      }
      if (activeStates.has(next.status)) timer = setTimeout(() => { void refresh() }, 2000)
    } else {
      if (!legacyAllowed) throw new Error('计算任务数据格式不正确。')
      result.value = normalize(value); task.value = null; taskId.value = null; persist(null)
      preserveResult = false
    }
  }
  async function perform(request: ComputeRequest, mode: 'start' | 'read' | 'cancel') {
    const current = session.value, requestKey = key
    if (!current || !requestKey) return
    stop()
    const sequence = serial
    const active = () => serial === sequence && key === requestKey && session.value?.cache_scope === current.cache_scope
    controller = new AbortController()
    const signal = controller.signal
    submitting.value = mode === 'start'; checking.value = mode === 'read'; cancelling.value = mode === 'cancel'
    error.value = undefined
    try {
      const response = mode === 'cancel'
        ? await api.write(request.path, {}, current.csrf_token, current.cache_scope, 'DELETE', signal)
        : request.method === 'GET' ? await api.read(request.path, signal, current.cache_scope)
          : await api.write(request.path, request.body ?? {}, current.csrf_token, current.cache_scope, 'POST', signal)
      if (active()) accept(response, mode === 'start')
    } catch (failure) {
      if (!active()) return
      error.value = failure
      if (mode !== 'start' && failure instanceof ApiError && failure.status === 404) {
        taskId.value = null; task.value = null; result.value = null; persist(null)
        error.value = new Error('任务不可用或已清理，请重新运行。')
      }
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
        persist(null)
        window.dispatchEvent(new Event('quant-auth-invalidated'))
      }
    } finally {
      if (active()) { submitting.value = false; checking.value = false; cancelling.value = false; controller = undefined }
    }
  }
  async function start(request: ComputeRequest, keepResult = false) {
    if (busy.value) return
    preserveResult = keepResult
    task.value = null; if (!keepResult) result.value = null
    taskId.value = null; persist(null)
    await perform(request, 'start')
  }
  async function refresh() {
    if (taskId.value) await perform({ path: `/compute/tasks/${encodeURIComponent(taskId.value)}`, method: 'GET' }, 'read')
  }
  async function cancel() {
    if (taskId.value && task.value && activeStates.has(task.value.status))
      await perform({ path: `/compute/tasks/${encodeURIComponent(taskId.value)}` }, 'cancel')
  }

  watch(() => session.value ? `guanlan.compute-task.v1:${JSON.stringify([session.value.user.id, session.value.cache_scope, kind])}` : null, next => {
    stop(); key = next
    preserveResult = false
    task.value = null; result.value = null; error.value = undefined; notice.value = ''; taskId.value = null
    if (!next) return
    let saved: string | null = null
    try { saved = sessionStorage.getItem(next) }
    catch { notice.value = '当前浏览器无法保存任务，刷新后无法自动恢复。' }
    if (saved && /^[\w-]{1,64}$/.test(saved)) {
      taskId.value = saved
      const restoring = refresh(), restoreSerial = serial
      void restoring.then(() => {
        if (serial === restoreSerial && key === next && autoRequest && task.value?.status === 'completed' && !error.value) void start(autoRequest, true)
      })
    } else if (autoRequest) void start(autoRequest)
  }, { immediate: true, flush: 'sync' })
  onScopeDispose(stop)
  return { task, result, error, notice, busy, requesting, cancelling, recoverable: computed(() => !!taskId.value), start, refresh, cancel }
}
