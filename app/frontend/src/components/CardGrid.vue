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
  (e: 'clone', note: Note): void
  (e: 'clients', note: Note): void
}>()

const filtered = computed(() => {
  const q = (props.filterText || '').trim().toLowerCase()
  if (!q) return props.notes
  // 多字段筛选：标题 / 作者昵称 / 笔记ID（feed_id），任一命中即显示
  return props.notes.filter(n =>
    (n.title || '').toLowerCase().includes(q) ||
    (n.nickname || '').toLowerCase().includes(q) ||
    (n.feed_id || '').toLowerCase().includes(q)
  )
})

function fmt(n: number | null | undefined): string {
  const v = n || 0
  return v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v)
}

/** 封面图：本地代理优先（同源无防盗链 403），图床直连兜底（no-referrer 绕过 Referer 校验） */
function coverOf(note: Note): string {
  return note.local_cover || coverProxy(note.cover_url || note.local_images?.[0] || '')
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

function openOrigin(note: Note) {
  const url = note.origin ||
    `https://www.xiaohongshu.com/explore/${note.feed_id}${note.xsec_token ? `?xsec_token=${note.xsec_token}` : ''}`
  window.open(url, '_blank')
}

function selectAll() {
  // 全选图文：只选中图文笔记（视频笔记暂不可克隆，仅图文可进克隆/导出）
  const visible = filtered.value
  const pics = visible.filter(n => n.note_type !== 'video')
  const vids = visible.length - pics.length
  pics.forEach(n => { appState.selected[n.feed_id] = true })
  toast(vids > 0 ? `已全选 ${pics.length} 条图文（${vids} 条视频已排除）` : `已全选 ${pics.length} 条图文`)
}

function clearSelect() {
  appState.selected = {}
  toast('已清空选择')
}

// 下载图片包（P3）：后端把选中笔记的本地封面打包 zip，走 blob 下载
async function downloadImages() {
  const ids = Object.keys(appState.selected)
  if (!ids.length) { toast('请先勾选笔记'); return }
  let resp
  try {
    resp = await fetch('/api/download_images', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feed_ids: ids }),
    })
  } catch {
    toast('打包失败：网络错误')
    return
  }
  if (!resp.ok) {
    toast('打包失败：' + ((await resp.text()) || resp.status).toString().slice(0, 80))
    return
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `xhs_images_${Date.now()}.zip`
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url) }, 1000)
  let missing = 0
  try { missing = JSON.parse(resp.headers.get('X-Missing') || '[]').length } catch { /* 忽略 */ }
  toast(missing > 0 ? `已打包 ${ids.length - missing} 张图片（${missing} 张缺失），文件已保存到浏览器下载目录` : `已打包 ${ids.length} 张图片，文件已保存到浏览器下载目录`)
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
  document.body.appendChild(a)   // 必须挂载 DOM 才会真正触发下载
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(a.href) }, 1000)  // 延迟 revoke，等下载启动
  toast(`已导出 ${data.length} 条 JSON，文件已保存到浏览器下载目录`)
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
  document.body.appendChild(a)   // 必须挂载 DOM 才会真正触发下载
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(a.href) }, 1000)  // 延迟 revoke
  toast(`已导出 ${dataCount()} 条 CSV，文件已保存到浏览器下载目录`)
}

function dataCount() {
  return props.notes.length
}

const selCount = computed(() => selectedCount())
</script>

<template>
  <!-- 顶部批量条（对齐视频：全选图文 / 清空选择 / 下载图片包 / 导出JSON，克隆走卡片单条） -->
  <div class="batch" v-if="selectable && notes.length" style="margin-bottom:14px;justify-content:flex-end">
    <div style="display:flex;gap:8px">
      <button class="b-btn" @click="selectAll">全选图文</button>
      <button class="b-btn" @click="clearSelect">清空选择</button>
      <button class="b-btn" @click="downloadImages">下载图片包</button>
      <button class="b-btn" @click="exportJSON">导出 JSON</button>
      <button class="b-btn" @click="exportCSV">导出 CSV</button>
    </div>
  </div>
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
          referrerpolicy="no-referrer"
          @error="onImgError(note)"
        />
        <div v-else class="ph" :style="phStyle(note)">{{ (note.title || '图')[0] }}</div>
        <span class="badge-note">{{ note.note_type === 'video' ? '视频' : '图文' }}</span>
        <span v-if="note.score != null" class="score-badge" :class="{ hot: note.score >= 90 }">{{ note.score.toFixed(1) }}</span>
        <button class="open-link" title="打开原作品（新窗口）" @click.stop="openOrigin(note)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4">
            <path d="M7 17 17 7M9 7h8v8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <span class="check">✓</span>
        <!-- 悬停浮层：单条操作（一键克隆仅图文，采集客户图文+视频均可） -->
        <div class="hover-actions">
          <button
            class="ha-btn clone"
            :class="{ off: note.note_type === 'video' }"
            :title="note.note_type === 'video' ? '视频暂不支持克隆' : '一键克隆（抄内容）'"
            @click.stop="note.note_type === 'video' ? toast('视频笔记暂不支持一键克隆（视频克隆待办中）') : emit('clone', note)"
          >一键克隆</button>
          <button class="ha-btn origin" title="打开小红书原作品页" @click.stop="openOrigin(note)">打开原文</button>
          <button class="ha-btn client" title="采集客户（采人）" @click.stop="emit('clients', note)">采集客户</button>
        </div>
      </div>
      <div class="meta">
        <div class="title">{{ note.title || '（无标题）' }}</div>
        <div class="row">
          <span class="author">
            <span class="avatar"></span>{{ note.nickname || '未知作者' }}
          </span>
          <span class="tag" :class="{ hot: (note.score ?? 0) >= 90 }">{{ (note.score ?? 0) >= 90 ? '最值得模仿' : '高评分候选' }}</span>
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
  <div v-if="!filtered.length" style="text-align:center;color:#AAA;padding:40px 0">暂无笔记（{{ props.filterText ? '无匹配结果（标题/作者/UID）' : '先点「开始采集」' }}）</div>
</template>
