import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { useFxRates } from '../src/composables/useFxRates.ts'

function storedFx(t, initial = {}) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const values = new Map(Object.entries(initial))
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) },
  })
  const scope = effectScope()
  t.after(() => {
    scope.stop()
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous)
    else delete globalThis.localStorage
  })
  return { open: () => scope.run(() => useFxRates()) }
}
const jpyRate = (rate) => ({ from: 'JPY', rate, source: 'synthetic fixture', observed_at: '2026-10-23T00:00:00Z' })

test('changing base currency cannot reinterpret a CNY rate as USD', (t) => {
  const fx = storedFx(t).open()
  fx.upsertRate(jpyRate(0.05))
  assert.equal(fx.convert(100, 'JPY').value, 5)
  fx.baseCurrency.value = 'USD'
  assert.equal(fx.convert(100, 'JPY').missing, true)
  fx.baseCurrency.value = 'CNY'
  assert.equal(fx.convert(100, 'JPY').value, 5)
})

test('the same original currency keeps independent rates for each base currency', (t) => {
  const fx = storedFx(t).open()
  fx.upsertRate(jpyRate(0.05))
  fx.baseCurrency.value = 'USD'
  fx.upsertRate(jpyRate(0.01))
  assert.equal(fx.convert(100, 'JPY').value, 1)
  fx.baseCurrency.value = 'CNY'
  assert.equal(fx.convert(100, 'JPY').value, 5)
  fx.removeRate('JPY')
  assert.equal(fx.convert(100, 'JPY').missing, true)
  fx.baseCurrency.value = 'USD'
  assert.equal(fx.convert(100, 'JPY').value, 1)
})

test('legacy rates bind to the saved base once and keep that meaning after reload', (t) => {
  const storage = storedFx(t, {
    'guanlan.fx.base.v1': JSON.stringify('CNY'),
    'guanlan.fx.rates.v1': JSON.stringify([{ ...jpyRate(0.05), updated_at: '2026-10-23T00:00:00Z' }]),
  })
  const fx = storage.open()
  assert.equal(fx.convert(100, 'JPY').value, 5)
  fx.baseCurrency.value = 'USD'
  const reloaded = storage.open()
  assert.equal(reloaded.convert(100, 'JPY').missing, true)
  reloaded.baseCurrency.value = 'CNY'
  assert.equal(reloaded.convert(100, 'JPY').value, 5)
})

test('a pending rates pull retains its original base when the selected base changes', async (t) => {
  const fx = storedFx(t).open()
  let release
  t.mock.method(
    globalThis,
    'fetch',
    () =>
      new Promise((resolve) => {
        release = resolve
      }),
  )
  fx.pullUrl.value = 'https://synthetic.invalid/rates'
  const pending = fx.pullRates()
  fx.baseCurrency.value = 'USD'
  release(Response.json([jpyRate(0.05)]))
  await pending
  assert.equal(fx.convert(100, 'JPY').missing, true)
  fx.baseCurrency.value = 'CNY'
  assert.equal(fx.convert(100, 'JPY').value, 5)
})

test('pulling CNY rates preserves previously saved USD pairs', async (t) => {
  const fx = storedFx(t).open()
  fx.baseCurrency.value = 'USD'
  fx.upsertRate(jpyRate(0.01))
  fx.baseCurrency.value = 'CNY'
  fx.pullUrl.value = 'https://synthetic.invalid/rates'
  t.mock.method(globalThis, 'fetch', async () => Response.json([jpyRate(0.05)]))
  await fx.pullRates()
  assert.equal(fx.convert(100, 'JPY').value, 5)
  fx.baseCurrency.value = 'USD'
  assert.equal(fx.convert(100, 'JPY').value, 1)
})

test('base and original currency codes are normalized for conversion', (t) => {
  const fx = storedFx(t, { 'guanlan.fx.base.v1': JSON.stringify('cny') }).open()
  assert.equal(fx.convert(10, 'CNY').missing, false)
  fx.upsertRate(jpyRate(0.05))
  assert.equal(fx.convert(100, 'jpy').value, 5)
})
