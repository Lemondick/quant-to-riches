<template>
  <div class="ask-widget">
    <button v-if="!open" class="ask-fab" aria-label="打开智能问答" @click="open = true">
      <MessageCircleQuestion :size="22" aria-hidden="true" />
    </button>
    <div v-else class="ask-panel panel" role="dialog" aria-label="智能问答">
      <div class="ask-head">
        <div><b>智能问答</b><span class="subtle">{{ liveBackend ? '账本问答 · 模板命中走确定性查询' : '演示模式' }}</span></div>
        <button class="icon-btn" aria-label="关闭智能问答" @click="open = false"><X :size="15" aria-hidden="true" /></button>
      </div>
      <div ref="scrollBox" class="ask-messages" aria-live="polite">
        <div v-for="(message, index) in messages" :key="index" class="ask-msg" :class="message.role">
          <span class="ask-bubble">{{ message.text }}</span>
          <span v-if="message.note" class="ask-note">{{ message.note }}</span>
        </div>
        <div v-if="sending" class="ask-msg assistant"><span class="ask-bubble typing">思考中…</span></div>
      </div>
      <div class="ask-chips" role="group" aria-label="高频问题">
        <button v-for="chip in ASK_QUICK_QUESTIONS" :key="chip" class="chip" :disabled="sending" @click="ask(chip)">{{ chip }}</button>
      </div>
      <form class="ask-input" @submit.prevent="submit">
        <input v-model.trim="draft" placeholder="输入问题，如：今年收益如何？" aria-label="输入问题" :disabled="sending" maxlength="200" />
        <button class="btn primary" type="submit" :disabled="sending || !draft" aria-label="发送问题"><Send :size="14" aria-hidden="true" /></button>
      </form>
    </div>
  </div>
</template>
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { MessageCircleQuestion, Send, X } from 'lucide-vue-next'
import { createDataApi, ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import { ASK_QUICK_QUESTIONS, type AskResponse } from '../services/batch2Api'
import { mockAskAnswer } from '../data/batch2'

const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
/** 会话仅用于取 csrf/scope；实际问答走直连 POST，避免触发全局缓存失效。 */
const { session } = useAccountResource('ask', (api, signal) => api.read<unknown>('/auth/session', signal))

type Message = { role: 'user' | 'assistant'; text: string; note?: string }
const open = ref(false)
const draft = ref('')
const sending = ref(false)
const scrollBox = ref<HTMLElement>()
const messages = ref<Message[]>([
  { role: 'assistant', text: '你好，我是观澜智能问答。点击下方高频问题，或直接输入你想了解的账户问题。' },
])

watch(messages, () => {
  void nextTick(() => { if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight })
}, { deep: true })

async function postAsk(question: string): Promise<AskResponse> {
  const current = session.value
  if (!current) throw new ApiError(401, '请先登录后端账户。')
  const client = createDataApi(import.meta.env.VITE_API_BASE_URL ?? '')
  return client.write<AskResponse>('/ask', { question }, current.csrf_token, current.cache_scope, 'POST')
}

async function ask(question: string) {
  const text = question.trim()
  if (!text || sending.value) return
  draft.value = ''
  messages.value.push({ role: 'user', text })
  sending.value = true
  try {
    if (liveBackend) {
      try {
        const response = await postAsk(text)
        messages.value.push({ role: 'assistant', text: response.answer, note: response.cost_note ?? (response.matched_template ? `命中模板：${response.matched_template}` : undefined) })
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 404) {
          messages.value.push({ role: 'assistant', text: mockAskAnswer(text), note: '后端问答接口尚未就绪（A 并行开发中），当前为演示答案。' })
        } else {
          throw failure
        }
      }
    } else {
      await new Promise(resolve => setTimeout(resolve, 300))
      messages.value.push({ role: 'assistant', text: mockAskAnswer(text) })
    }
  } catch (failure) {
    messages.value.push({ role: 'assistant', text: failure instanceof Error ? failure.message : '问答失败，请稍后重试。' })
  } finally {
    sending.value = false
  }
}

function submit() { void ask(draft.value) }
</script>
<style scoped>
.ask-widget{position:fixed;right:20px;bottom:20px;z-index:1200}
.ask-fab{width:52px;height:52px;border-radius:50%;border:1px solid rgba(75,140,255,.5);background:linear-gradient(135deg,#3178f5,#5b82f0);color:#fff;display:grid;place-items:center;cursor:pointer;box-shadow:0 6px 24px rgba(46,116,242,.4);transition:transform .2s}
.ask-fab:hover{transform:scale(1.06)}
.ask-panel{width:min(380px,calc(100vw - 40px));height:520px;max-height:calc(100vh - 120px);display:flex;flex-direction:column;overflow:hidden}
.ask-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border)}
.ask-head b{font-size:14px;margin-right:8px}
.subtle{font-size:10px;color:var(--text-2)}
.ask-messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.ask-msg{display:flex;flex-direction:column;gap:2px;max-width:92%}
.ask-msg.user{align-self:flex-end;align-items:flex-end}
.ask-msg.assistant{align-self:flex-start}
.ask-bubble{font-size:12.5px;line-height:1.6;padding:8px 12px;border-radius:12px;background:rgba(34,51,77,.7);color:var(--text-1);white-space:pre-wrap;word-break:break-word}
.ask-msg.user .ask-bubble{background:linear-gradient(135deg,#3178f5,#5b82f0);color:#fff}
.ask-bubble.typing{opacity:.7}
.ask-note{font-size:10px;color:var(--text-2)}
.ask-chips{display:flex;flex-wrap:wrap;gap:6px;padding:10px 12px;border-top:1px solid var(--border);max-height:96px;overflow-y:auto}
.chip{font-size:11px;padding:5px 10px;border-radius:14px;border:1px solid rgba(43,66,101,.7);background:rgba(20,37,62,.7);color:#9fc0ee;cursor:pointer;transition:all .2s}
.chip:hover:not(:disabled){border-color:var(--accent);color:#fff}
.chip:disabled{opacity:.5;cursor:default}
.ask-input{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border)}
.ask-input input{flex:1;height:36px;border:1px solid rgba(43,66,101,.7);border-radius:8px;background:rgba(20,37,62,.7);padding:0 12px;color:var(--text-1);font-size:13px}
</style>
