<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { apiLoginStatus, apiQrcode } from '../api'
import { appState, toast } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const guide = ref('')
const guideOk = ref(false)
const qrError = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

async function openXhs() {
  loading.value = true
  qrError.value = ''
  try {
    const r = await apiQrcode()
    if (r.ok) {
      guideOk.value = true
      guide.value = r.guide || '请在打开的 Chrome 小红书首页中扫码登录'
    } else {
      guideOk.value = false
      qrError.value = r.error || '打开失败'
    }
  } catch (e) {
    qrError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function pollLogin() {
  try {
    const lg = await apiLoginStatus()
    if (lg.logged_in) {
      appState.login = { logged_in: true, nickname: lg.nickname || '' }
      appState.cookieRefreshTick += 1 // 通知 Cookie 区刷新（Chrome 内登录后 cookie 已更新）
      toast('登录成功，已恢复小红书登录态')
      stopTimers()
      emit('close')
    }
  } catch (e) {
    /* 后端暂不可达时静默 */
  }
}

function stopTimers() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

onMounted(() => {
  openXhs()
  pollTimer = setInterval(pollLogin, 4000)
})

onUnmounted(stopTimers)
</script>

<template>
  <div v-if="props.open" class="modal-mask" @click.self="emit('close')">
    <div class="modal qr-modal">
      <h3>登录小红书</h3>
      <p class="qr-tip">
        小红书登录态已失效。已在你打开的 Chrome 中打开小红书首页，请直接扫码登录
        （推荐使用当前演示账号），登录成功后本窗口自动关闭。
      </p>
      <div class="qr-stage">
        <div v-if="loading" class="qr-loading">正在打开 Chrome 小红书首页…</div>
        <div v-else-if="guideOk" class="qr-guide">
          <p class="guide-main">{{ guide }}</p>
          <p class="guide-sub">登录后自动继续（每 4s 检测一次登录状态）</p>
        </div>
        <div v-else class="qr-error">
          <p>{{ qrError }}</p>
          <button class="btn" @click="openXhs">重试打开</button>
        </div>
      </div>
      <div class="qr-meta">
        <span v-if="guideOk">检测中…</span>
        <span v-else>&nbsp;</span>
        <button class="btn" @click="openXhs" :disabled="loading">重新打开</button>
      </div>
      <button class="close" @click="emit('close')">关闭</button>
    </div>
  </div>
</template>

<style scoped>
.qr-modal { width: 380px; text-align: center; }
.qr-tip { font-size: 13px; color: #666; line-height: 1.6; margin: 8px 0 12px; }
.qr-stage {
  min-height: 200px; display: flex; align-items: center; justify-content: center;
  background: #fff; border: 1px solid #eee; border-radius: 8px; overflow: hidden;
  padding: 16px;
}
.qr-guide .guide-main { font-size: 14px; color: #333; line-height: 1.6; }
.qr-guide .guide-sub { font-size: 12px; color: #999; margin-top: 8px; }
.qr-loading, .qr-error { color: #999; font-size: 13px; }
.qr-error .btn { margin-top: 10px; }
.qr-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 12px; color: #888; }
</style>
