<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiLoginStatus, apiQrcode, apiRefreshCookieFromChrome } from '../api'
import { appState, toast } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const checking = ref(false)
const guide = ref('')
const guideOk = ref(false)
const qrError = ref('')

const autoReading = ref(false)
const autoMsg = ref('')

// 方案 A：直接读日常 Chrome 的 Cookie 库（钥匙串解密），无需手动扫码/复制粘贴
async function autoReadChrome() {
  if (autoReading.value) return
  autoReading.value = true
  autoMsg.value = ''
  try {
    const r = await apiRefreshCookieFromChrome()
    if (r.ok) {
      appState.login = { logged_in: true, nickname: r.account || '已登录' }
      appState.cookieRefreshTick += 1
      toast(r.note || '已从 Chrome 自动恢复登录态')
      emit('close')
    } else if (r.need_auth) {
      autoMsg.value = '首次使用需要授权：系统已弹出钥匙串授权窗口，请在其中输入密码并点「始终允许」，然后点击下方「我已授权，重试」'
    } else {
      autoMsg.value = r.error || '自动读取失败，可改用下方扫码方式'
    }
  } catch (e) {
    autoMsg.value = (e as Error).message || '自动读取失败'
  } finally {
    autoReading.value = false
  }
}

function retryAutoRead() {
  autoMsg.value = ''
  autoReadChrome()
}

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

// 手动确认检测：不自动轮询、不主动触发 Chrome CDP 连接（避免反复弹「远程调试授权」窗）。
// 用户扫码完成后点「我已扫码」，才发起一次登录态检测。
async function checkLogin() {
  if (checking.value) return
  checking.value = true
  try {
    const lg = await apiLoginStatus()
    if (lg.logged_in) {
      appState.login = { logged_in: true, nickname: lg.nickname || '' }
      appState.cookieRefreshTick += 1 // 通知 Cookie 区刷新（Chrome 内登录后 cookie 已更新）
      toast('登录成功，已恢复小红书登录态')
      emit('close')
    } else {
      toast('仍未检测到登录态，请确认已扫码成功')
    }
  } catch (e) {
    toast('检测失败：' + ((e as Error).message || '后端不可达'))
  } finally {
    checking.value = false
  }
}

onMounted(openXhs)
</script>

<template>
  <div v-if="props.open" class="modal-mask" @click.self="emit('close')">
    <div class="modal qr-modal">
      <h3>登录小红书</h3>
      <p class="qr-tip">
        小红书登录态已失效。可一键读取本机 Chrome 的登录态恢复（无需复制粘贴）；
        或改用下方扫码登录。
      </p>
      <div class="auto-row">
        <button class="btn auto-btn" @click="autoReadChrome" :disabled="autoReading">
          {{ autoReading ? '正在读取…' : '一键读取 Chrome 登录态' }}
        </button>
        <button v-if="autoMsg && autoMsg.includes('重试')" class="btn auto-retry" @click="retryAutoRead">我已授权，重试</button>
        <p v-if="autoMsg" class="auto-msg">{{ autoMsg }}</p>
      </div>
      <div class="qr-stage">
        <div v-if="loading" class="qr-loading">正在打开 Chrome 小红书首页…</div>
        <div v-else-if="guideOk" class="qr-guide">
          <p class="guide-main">{{ guide }}</p>
          <p class="guide-sub">扫码完成后点击「我已扫码完成」检测登录态</p>
          <button class="btn check-btn" @click="checkLogin" :disabled="checking">
            {{ checking ? '检测中…' : '我已扫码完成' }}
          </button>
        </div>
        <div v-else class="qr-error">
          <p>{{ qrError }}</p>
          <button class="btn" @click="openXhs">重试打开</button>
        </div>
      </div>
      <div class="qr-meta">
        <span v-if="guideOk">手动检测模式（不自动连接）</span>
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
.auto-row { margin: 0 0 10px; }
.auto-btn { min-width: 200px; }
.auto-retry { margin-top: 8px; }
.auto-msg { font-size: 12px; color: #c0392b; line-height: 1.5; margin-top: 8px; padding: 0 8px; }
.qr-stage {
  min-height: 200px; display: flex; align-items: center; justify-content: center;
  background: #fff; border: 1px solid #eee; border-radius: 8px; overflow: hidden;
  padding: 16px;
}
.qr-guide .guide-main { font-size: 14px; color: #333; line-height: 1.6; }
.qr-guide .guide-sub { font-size: 12px; color: #999; margin-top: 8px; }
.qr-loading, .qr-error { color: #999; font-size: 13px; }
.qr-error .btn { margin-top: 10px; }
.check-btn { margin-top: 14px; min-width: 120px; }
.qr-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 12px; color: #888; }
</style>
