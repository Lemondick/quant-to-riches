import { ref, watch, type Ref } from 'vue'

export const localStorageAvailable = ref(true)

export function useLocalState<T>(key: string, initial: T, validate: (value: unknown) => value is T) {
  let value = structuredClone(initial)
  let raw: string | null = null
  try { raw = localStorage.getItem(key) } catch { localStorageAvailable.value = false }
  try {
    const stored: unknown = JSON.parse(raw ?? 'null')
    if (validate(stored)) value = stored
  } catch { /* Local state still works when storage is unavailable. */ }
  const state = ref(value) as Ref<T>
  watch(state, value => {
    try { localStorage.setItem(key, JSON.stringify(value)) } catch { localStorageAvailable.value = false }
  }, { deep: true, flush: 'sync' })
  return state
}
