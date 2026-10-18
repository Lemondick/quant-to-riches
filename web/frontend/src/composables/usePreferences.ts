import { useLocalState } from './useLocalState'

export const defaultPreferences = {
  username: 'admin', displayName: '观澜研究员', email: 'admin@example.com', timezone: 'Asia/Shanghai', avatarColor: '#4d8dff',
  market: '中国 A 股', dailyReminder: true, policyReminder: true, marketColors: 'green-up', density: 'comfortable', appearance: 'standard', reducedMotion: false
}
export type Preferences = typeof defaultPreferences
const preferences = useLocalState<Preferences>('guanlan.preferences.v1', defaultPreferences, (value): value is Preferences => {
  if (!value || typeof value !== 'object') return false
  const item = value as Preferences
  return ['username', 'displayName', 'email', 'timezone', 'avatarColor', 'market'].every(key => typeof item[key as keyof Preferences] === 'string') && ['Asia/Shanghai', 'Asia/Tokyo'].includes(item.timezone) && ['green-up', 'red-up'].includes(item.marketColors) && ['comfortable', 'compact'].includes(item.density) && ['standard', 'soft'].includes(item.appearance) && [item.dailyReminder, item.policyReminder, item.reducedMotion].every(value => typeof value === 'boolean')
})
export function usePreferences() { return { preferences } }
