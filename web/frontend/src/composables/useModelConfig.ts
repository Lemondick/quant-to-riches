import { reactive, ref, watch } from 'vue'
import { localStorageAvailable } from './useLocalState'
import { copyModelConfiguration, defaultModelConfiguration, isModelConfiguration, type ModelConfiguration } from '../utils/modelConfig'

const storageKey = 'guanlan.model-config.v1'
const sessionKey = 'guanlan.model-keys.v1'
let initial = copyModelConfiguration(defaultModelConfiguration)
try {
  const stored: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
  if (isModelConfiguration(stored)) initial = copyModelConfiguration(stored)
} catch { /* Invalid or unavailable storage leaves the initial configuration usable. */ }

const configuration = ref(initial)
const keys = reactive({ compatible: '', jev: '' })
const sessionAvailable = ref(true)
try {
  const stored = JSON.parse(sessionStorage.getItem(sessionKey) ?? 'null')
  if (stored && typeof stored === 'object') {
    keys.compatible = typeof stored.compatible === 'string' ? stored.compatible : ''
    keys.jev = typeof stored.jev === 'string' ? stored.jev : ''
  }
} catch { sessionAvailable.value = false }

watch(keys, value => {
  try {
    if (value.compatible || value.jev) sessionStorage.setItem(sessionKey, JSON.stringify({ compatible: value.compatible, jev: value.jev }))
    else sessionStorage.removeItem(sessionKey)
  } catch { sessionAvailable.value = false }
}, { flush: 'sync' })

function saveConfiguration(value: ModelConfiguration) {
  configuration.value = copyModelConfiguration(value)
  try { localStorage.setItem(storageKey, JSON.stringify(configuration.value)) }
  catch { localStorageAvailable.value = false }
}

export function useModelConfig() { return { configuration, keys, sessionAvailable, saveConfiguration } }
