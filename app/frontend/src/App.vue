<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { appState, setStage, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import CardMenu from './components/CardMenu.vue'
import PublishPage from './components/PublishPage.vue'
import LoginModal from './components/LoginModal.vue'
import { apiLoginStatus, apiRefreshCookieFromChrome, apiCleanupImages, apiNotes } from './api'

const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)

const menuNote = ref<Note | null>(null)
const menuPos = ref({ x: 0, y: 0 })

const loginOpen = ref(false)
// 登录态检测改为「按需触发」：不再自动调用/定时轮询
// （每次检测都会发起 Chrome CDP 连接 → 必弹「远程调试授权」窗）。
// 触发点：用户主动打开登录引导；或搜索/采集失败提示后手动打开。
function checkLogin() {
  apiLoginStatus().then((lg) => {
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '' }
    if (!lg.logged_in && !appState.collecting) loginOpen.value = true
  }).catch(() => { /* 后端未就绪静默 */ })
}

function openMenu(payload: { note: Note; x: number; y: number }) {
  menuNote.value = payload.note
  menuPos.value = { x: payload.x, y: payload.y }
}

function closeMenu() {
  menuNote.value = null
}

async function openPublish(note: Note) {
  menuNote.value = null
  appState.publishOpen = true
  // v-if 渲染 PublishPage 是异步的，必须等 DOM 挂载后才能拿到 ref
  await nextTick()
  await publishRef.value?.openFor(note)
}

// 单条克隆（对齐视频：克隆走卡片单条，不批量）
async function cloneOne(note: Note) {
  if (note.note_type === 'video') {
    toast('视频笔记暂不支持一键克隆（视频克隆待办中）')
    return
  }
  appState.publishOpen = true
  await nextTick()
  await publishRef.value?.openFor(note)
}

// 采集客户（P4）：真实浏览器抓取笔记评论区互动用户，入库去重；图文+视频均可
async function collectClients(note: Note) {
  const t = (note.title || '该笔记').slice(0, 12)
  toast(`正在采集「${t}…」的互动用户…`)
  try {
    const resp = await fetch('/api/clients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feed_id: note.feed_id, xsec_token: note.xsec_token || '' }),
    })
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (body?.detail) msg = String(body.detail)
      } catch { /* 非 JSON 错误体 */ }
      toast('采集失败：' + msg.slice(0, 120))
      if (msg.includes('登录态')) loginOpen.value = true
      return
    }
    const data = await resp.json()
    toast(data.total > 0
      ? `采集到 ${data.total} 位互动用户（新增 ${data.new}），已入库`
      : '未采集到互动用户（可能无评论或需登录）')
  } catch {
    toast('采集失败：网络错误')
  }
}

function backFromPublish() {
  appState.publishOpen = false
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

// 启动/刷新时自动检测登录态（生产 profile 模式：独立 Chrome 自动拉起+自动注入，无 CDP 授权弹窗代价）。
// 未登录 → 自动尝试方案 A（读日常 Chrome Cookie + 钥匙串解密 + 校验落盘）：
//   成功 → 登录态恢复、弹窗不出现；失败 → 弹登录引导（含登录链接 + 刷新自动恢复提示）。
// 用户在小红书完成登录后切回本页刷新，即重走本链路完成恢复。
onMounted(async () => {
  let lg: any = null
  try { lg = await apiLoginStatus() } catch { lg = null }
  const loggedIn = !!(lg && lg.logged_in)
  appState.login = { logged_in: loggedIn, nickname: (lg && lg.nickname) || '' }
  if (!loggedIn) {
    // 先弹登录引导（用户立即可见），后台同时自动尝试方案 A 读取日常 Chrome：
    // 成功 → 自动关闭弹窗并恢复；失败 → 弹窗保持，按引导「登录 → 回来刷新」完成恢复
    if (!appState.collecting) loginOpen.value = true
    try {
      const r = await apiRefreshCookieFromChrome()
      if (r && r.ok) {
        appState.login = { logged_in: true, nickname: r.account || '已登录' }
        appState.cookieRefreshTick += 1
        loginOpen.value = false
        toast(r.note || '已自动从 Chrome 恢复登录态')
      }
    } catch { /* 后台读取失败：弹窗引导保持 */ }
  }
  // 搜索结果用完即失效：启动时不加载历史 notes（每次进入保持空白，仅当次采集后展示）
  // （此前会自动加载上次结果并跳转筛选阶段，与「重新进入应为白屏」的规则冲突，已移除）
  // 启动时清理无引用本地图片（与后端 startup 双保险，防旧图堆积；fire-and-forget 不阻塞）
  apiCleanupImages().catch(() => {})
  // 显式 ?stage=pick 调试入口：不改变默认白屏规则，仅当 URL 显式带参时进入列表视图（UI 自动化/演示用）
  const urlStage = new URLSearchParams(location.search).get('stage')
  if (urlStage === 'pick') {
    try {
      const r = await apiNotes()
      if (r.total > 0) {
        appState.notes = r.notes
        appState.total = r.total
        appState.count = r.total
        appState.status = '展示中'
      }
    } catch { /* 列表加载失败：仍进入 pick 空态 */ }
    setStage('pick')
  }
})
</script>

<template>
  <div>
    <FlowBar />

    <template v-if="!appState.publishOpen">
      <div class="workbench">
        <CollectPanel @open-login="loginOpen = true" />
        <main class="work">
          <WorkArea @menu="openMenu" @clone="cloneOne" @clients="collectClients" />
        </main>
      </div>
    </template>

    <template v-else>
      <PublishPage ref="publishRef" @back="backFromPublish" />
    </template>

    <CardMenu :note="menuNote" :x="menuPos.x" :y="menuPos.y" @clone="openPublish" @close="closeMenu" />

    <!-- 登录二维码弹窗（Cookie 失效时自动弹出 / 可手动触发） -->
    <LoginModal :open="loginOpen" @close="loginOpen = false" />

    <!-- 全局点击关闭菜单 -->
    <div v-if="menuNote" style="position:fixed;inset:0;z-index:55" @click="closeMenu"></div>

    <!-- Toast -->
    <transition name="toast-fade">
      <div v-if="appState.toastMsg" class="toast">{{ appState.toastMsg }}</div>
    </transition>
  </div>
</template>

<style scoped>
.toast-fade-enter-active, .toast-fade-leave-active { transition: opacity .2s; }
.toast-fade-enter-from, .toast-fade-leave-to { opacity: 0; }
</style>
