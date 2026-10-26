<script setup lang="ts">
import { computed } from 'vue'
import type { Note } from '../types'
import { coverProxy } from '../store'
import { appState, selectedCount, toast } from '../store'

const props = defineProps<{
  notes: Note[]
  selectable?: boolean
  filterText?: string
}>()

const emit = defineEmits<{
  (e: 'menu', payload: { note: Note; x: number; y: number }): void
}>()

const filtered = computed(() => {
  const q = (props.filterText || '').trim()
  if (!q) return props.notes
  return props.notes.filter(n => (n.title || '').includes(q))
})

function fmt(n: number | null | undefined): string {
  const v = n || 0
  return v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v)
}

/** 封面图：本地代理优先（防盗链），其次外链，再占位 */
function coverOf(note: Note): string {
  return coverProxy(note.local_images?.[0] || note.local_cover || note.cover_url)
}

const broken = new Set<string>()
function onImgError(note: Note) {
  broken.add(note.feed_id)
}function phStyle(note: Note): Record<string, string> {
  const palette = ['#9A6A2F', '#7B8C6B', '#B4553D', '#5A7D8C', '#8C6B7B']
  let h = 0
  for (const ch of note.feed_id || '') h = (h * 31 + ch.charCodeAt(0)) % 997
  return { background: palette[h % palette.length] }
}

function onCardClick(note: Note, ev: MouseEvent) {
  if (props.selectable) {
    // 勾选模式：点击卡片切换选中；点右上角勾选标记同样切换
    const feedId = note.feed_id
    if (appState.selected[feedId]) delete appState.selected[feedId]
    else appState.selected[feedId] = true
    return
  }
  emit('menu', { note, x: ev.clientX, y: ev.clientY })
}

function isSel(note: Note) {
  return !!appState.selected[note.feed_id]
}

function selectAll() {
  props.notes.forEach(n => { appState.selected[n.feed_id] = true })
  toast(`已全选 ${props.notes.length} 条图文`)
}

function exportJSON() {
  const data = props.notes.map(n => ({
    feed_id: n.feed_id, title: n.title, nickname: n.nickname,
    liked_count: n.liked_count, collected_count: n.collected_count,
    comment_count: n.comment_count, desc: n.desc, cover_url: n.cover_url,
    origin: n.xsec_token ? `https://www.xiaohongshu.com/explore/${n.feed_id}?xsec_token=${n.xsec_token}` : ''
  }))
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `xhs_notes_${Date.now()}.json`
  a.click()
  URL.revokeObjectURL(a.href)
  toast(`已导出 ${data.length} 条 JSON`)
}

function exportCSV() {
  const rows = props.notes.map(n => [
    n.title || '', n.nickname || '', n.liked_count || 0,
    n.collected_count || 0, n.comment_count || 0,
    n.desc ? n.desc.replace(/[\r\n]+/g, ' ') : ''
  ])
  const csv = ['标题,作者,点赞,收藏,评论,正文摘要', ...rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))].join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `xhs_notes_${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
  toast(`已导出 ${dataCount()} 条 CSV`)
}

function dataCount() {
  return props.notes.length
}

const selCount = computed(() => selectedCount())
</script>

<template>
  <div class="grid">
    <div
      v-for="note in filtered"
      :key="note.feed_id"
      class="card"
      :class="{ sel: isSel(note) }"
      @click="onCardClick(note, $event)"
    >
      <div class="cover">
        <img
          v-if="coverOf(note) && !broken.has(note.feed_id)"
          :src="coverOf(note)"
          alt=""
          loading="lazy"
          @error="onImgError(note)"
        />
        <div v-else class="ph" :style="phStyle(note)">{{ (note.title || '图')[0] }}</div>
        <span class="badge-note">{{ note.note_type === 'video' ? '视频' : '图文' }}</span>
        <span class="check">✓</span>
      </div>
      <div class="meta">
        <div class="title">{{ note.title || '（无标题）' }}</div>
        <div class="row">
          <span class="author">
            <span class="avatar"></span>{{ note.nickname || '未知作者' }}
          </span>
          <span class="interact">
            <svg width="11" height="11" viewBox="0 0 24 24" :fill="(note.liked_count || 0) > 10000 ? '#FF2442' : '#888'">
              <path d="M12 21s-7.5-4.6-10-9.2C.4 8.6 2.8 5 6.4 5c2 0 3.6 1 4.8 2.6C12.4 6 14 5 16 5c3.6 0 6 3.6 4.4 6.8C18.9 16.4 12 21 12 21z" />
            </svg>
            {{ fmt(note.liked_count) }}
          </span>
        </div>
      </div>
    </div>
  </div>
  <div v-if="!filtered.length" style="text-align:center;color:#AAA;padding:40px 0">暂无笔记（{{ props.filterText ? '无匹配标题' : '先点「开始搜索」' }}）</div>
  <div class="batch" v-if="selectable && notes.length" style="margin-top:14px;justify-content:flex-end">
    <button class="b-btn" @click="selectAll">全选图文</button>
    <button class="b-btn" @click="toast('演示：下载选中图片 zip（P3 实现）')">下载图片</button>
    <button class="b-btn" @click="exportJSON">导出 JSON</button>
    <button class="b-btn" @click="exportCSV">导出 CSV</button>
  </div>
</template>
