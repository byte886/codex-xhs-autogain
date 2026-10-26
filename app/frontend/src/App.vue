<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { appState, setStage, selectedNotes, toast } from './store'
import type { Note } from './types'
import FlowBar from './components/FlowBar.vue'
import CollectPanel from './components/CollectPanel.vue'
import WorkArea from './components/WorkArea.vue'
import FloatBar from './components/FloatBar.vue'
import CardMenu from './components/CardMenu.vue'
import PublishPage from './components/PublishPage.vue'

const publishRef = ref<InstanceType<typeof PublishPage> | null>(null)

const menuNote = ref<Note | null>(null)
const menuPos = ref({ x: 0, y: 0 })

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

async function cloneAll() {
  const notes = selectedNotes()
  if (!notes.length) return
  // 视频笔记暂不支持一键克隆（需视频下载+视频上传通道，P 待办），排除并提示
  const pics = notes.filter(n => n.note_type !== 'video')
  const vids = notes.length - pics.length
  if (!pics.length) {
    toast('所选均为视频笔记，暂不支持一键克隆（视频克隆待办中）')
    return
  }
  if (vids > 0) toast(`已跳过 ${vids} 条视频笔记（暂不支持视频克隆）`)
  // 批量克隆：用第一张图文笔记进入发布页（其余排队处理）
  const first = pics[0]
  appState.publishOpen = true
  await nextTick()
  await publishRef.value?.openFor(first)
  if (pics.length > 1) toast(`已进入第 1 条（共 ${pics.length} 条），其余可在发布后继续`)
  appState.selected = {}
}

function collectClients() {
  const notes = selectedNotes()
  toast(`演示：采集 ${notes.length} 条笔记的互动用户名单（P4 实现）`)
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
        <CollectPanel />
        <main class="work">
          <WorkArea @menu="openMenu" />
        </main>
      </div>
      <FloatBar v-if="appState.stage === 'pick'" @clone-all="cloneAll" @collect-clients="collectClients" />
    </template>

    <template v-else>
      <PublishPage ref="publishRef" @back="backFromPublish" />
    </template>

    <CardMenu :note="menuNote" :x="menuPos.x" :y="menuPos.y" @clone="openPublish" @close="closeMenu" />

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
