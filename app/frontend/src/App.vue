<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { appState, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import CardMenu from './components/CardMenu.vue'
import PublishPage from './components/PublishPage.vue'
import LoginModal from './components/LoginModal.vue'
import { apiLoginStatus } from './api'

const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)

const menuNote = ref<Note | null>(null)
const menuPos = ref({ x: 0, y: 0 })

const loginOpen = ref(false)
function checkLogin() {
  apiLoginStatus().then((lg) => {
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '' }
    // 容器登录态失效时自动弹出扫码窗口
    if (!lg.logged_in && !appState.collecting) loginOpen.value = true
  }).catch(() => { /* 后端未就绪静默 */ })
}
checkLogin()

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

// 采集客户：单条（图文+视频均可），P4 未实现前为演示
function collectClients(note: Note) {
  const t = (note.title || '该笔记').slice(0, 12)
  toast(`演示：采集「${t}…」的互动用户名单（P4 实现）`)
}

function backFromPublish() {
  appState.publishOpen = false
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}
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
