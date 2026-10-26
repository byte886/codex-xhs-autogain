<script setup lang="ts">
import { ref } from 'vue'
import { appState } from '../store'
import type { Note } from '../types'
import CardGrid from './CardGrid.vue'
import FilterRank from './FilterRank.vue'

const emit = defineEmits<{ (e: 'menu', payload: { note: Note; x: number; y: number }): void }>()

const filterText = ref('')

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function cloneFromRank(n: Note) {
  emit('menu', { note: n, x: Math.max(window.innerWidth - 220, 200), y: 220 })
}
</script>

<template>
  <div>
    <!-- 待输入 -->
    <div v-if="appState.stage === 'input'" class="empty">
      <div class="big">待输入</div>
      <p>在左侧填写关键词并设置参数，点击「开始搜索」</p>
      <p class="sub">（视频示例："比如说关键词输入一个减肥"）</p>
    </div>

    <!-- 搜索作品 -->
    <div v-else-if="appState.stage === 'search'">
      <div class="info-bar">
        <div class="info-item">
          <span class="k">共获取</span><span class="v">{{ appState.total || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="k">当前任务</span><span class="v small">{{ appState.taskLabel || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="k">状态</span>
          <span class="status-pill" :class="{ running: appState.status === '采集中', done: appState.status === '展示中' }">{{ appState.status }}</span>
        </div>
        <div class="info-item">
          <span class="k">已采集</span><span class="v">{{ appState.notes.length }}</span>
          <span class="v small">{{ fmtTime(appState.elapsed) }}</span>
        </div>
        <div class="filter-title">
          <input v-model="filterText" placeholder="筛选标题…" style="width:100%" />
        </div>
      </div>
      <CardGrid :notes="appState.notes" :filter-text="filterText" @menu="emit('menu', $event)" />
      <div v-if="appState.searchNote" style="margin-top:12px;font-size:12px;color:var(--ink-2);background:#FFF7E6;border-radius:8px;padding:8px 12px">
        {{ appState.searchNote }}
      </div>
    </div>

    <!-- 筛选结果 -->
    <div v-else-if="appState.stage === 'filter'">
      <div class="info-bar">
        <div class="info-item">
          <span class="k">评分模型</span><span class="v small">互动密度 × 内容结构 × 爆款潜力</span>
        </div>
      </div>
      <FilterRank :notes="appState.notes" @clone="cloneFromRank" />
    </div>

    <!-- 选择作品 -->
    <div v-else-if="appState.stage === 'pick'">
      <div class="info-bar">
        <div class="info-item">
          <span class="k">已选择</span><span class="v">{{ Object.keys(appState.selected).length }}</span>
        </div>
        <div class="info-item">
          <span class="k">操作提示</span><span class="v small">点击卡片勾选，右下角悬浮按钮批量操作</span>
        </div>
      </div>
      <CardGrid :notes="appState.notes" selectable @menu="emit('menu', $event)" />
    </div>
  </div>
</template>
