<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { apiLoginStatus, apiQrcode } from '../api'
import { appState, toast } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const qrImage = ref('')
const qrError = ref('')
const qrCountdown = ref(90)
let timer: ReturnType<typeof setInterval> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

async function refreshQr() {
  loading.value = true
  qrError.value = ''
  try {
    const r = await apiQrcode()
    if (r.ok) {
      qrImage.value = `data:image/png;base64,${r.image}`
      qrCountdown.value = r.expires_in || 90
    } else {
      qrError.value = r.error || '二维码获取失败'
      qrImage.value = ''
    }
  } catch (e) {
    qrError.value = (e as Error).message
    qrImage.value = ''
  } finally {
    loading.value = false
  }
}

async function pollLogin() {
  try {
    const lg = await apiLoginStatus()
    if (lg.logged_in) {
      appState.login = { logged_in: true, nickname: lg.nickname || '' }
      appState.cookieRefreshTick += 1 // 通知 Cookie 区刷新（容器扫码后 cookies.json 已更新）
      toast('登录成功，已恢复容器登录态')
      stopTimers()
      emit('close')
    }
  } catch (e) {
    /* 后端暂不可达时静默 */
  }
}

function stopTimers() {
  if (timer) { clearInterval(timer); timer = null }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

onMounted(() => {
  refreshQr()
  timer = setInterval(() => {
    qrCountdown.value -= 1
    if (qrCountdown.value <= 0) refreshQr()
  }, 1000)
  pollTimer = setInterval(pollLogin, 4000)
})

onUnmounted(stopTimers)
</script>

<template>
  <div v-if="props.open" class="modal-mask" @click.self="emit('close')">
    <div class="modal qr-modal">
      <h3>扫码登录小红书</h3>
      <p class="qr-tip">
        容器内登录态已失效。请用手机小红书 App 扫码登录
        （推荐使用当前演示账号），登录成功后自动继续。
      </p>
      <div class="qr-stage">
        <div v-if="loading" class="qr-loading">正在获取二维码…</div>
        <img v-else-if="qrImage" :src="qrImage" alt="登录二维码" class="qr-img" />
        <div v-else class="qr-error">
          <p>{{ qrError }}</p>
          <button class="btn" @click="refreshQr">重试获取二维码</button>
        </div>
      </div>
      <div class="qr-meta">
        <span v-if="qrImage">二维码 {{ qrCountdown }}s 后刷新</span>
        <span v-else>&nbsp;</span>
        <button class="btn" @click="refreshQr" :disabled="loading">刷新二维码</button>
      </div>
      <button class="close" @click="emit('close')">关闭</button>
    </div>
  </div>
</template>

<style scoped>
.qr-modal { width: 380px; text-align: center; }
.qr-tip { font-size: 13px; color: #666; line-height: 1.6; margin: 8px 0 12px; }
.qr-stage {
  min-height: 300px; display: flex; align-items: center; justify-content: center;
  background: #fff; border: 1px solid #eee; border-radius: 8px; overflow: hidden;
}
.qr-img { width: 300px; height: auto; }
.qr-loading, .qr-error { color: #999; font-size: 13px; }
.qr-error .btn { margin-top: 10px; }
.qr-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 12px; color: #888; }
</style>
