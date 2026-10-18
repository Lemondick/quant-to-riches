import { ref, watch } from 'vue'
import { fundList } from '../data/funds'

function loadSavedFunds(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem('guanlan.followed-funds.v1') ?? 'null')
    if (Array.isArray(value)) return [...new Set(value.filter((code): code is string => typeof code === 'string' && fundList.some(fund => fund.code === code)))]
  } catch { /* Use the initial demo selection when storage is unavailable. */ }
  return ['512000', '510300']
}

const savedFunds = ref(loadSavedFunds())
watch(savedFunds, value => {
  try { localStorage.setItem('guanlan.followed-funds.v1', JSON.stringify(value)) } catch { /* Keep the selection for this session. */ }
}, { deep: true })

export function useFollowedFunds() {
  const isSaved = (code: string) => savedFunds.value.includes(code)
  function toggleSave(code: string) {
    if (!fundList.some(fund => fund.code === code)) return
    savedFunds.value = isSaved(code) ? savedFunds.value.filter(saved => saved !== code) : [...savedFunds.value, code]
  }
  return { savedFunds, isSaved, toggleSave }
}
