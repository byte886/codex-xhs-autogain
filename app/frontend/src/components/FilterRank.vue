<script setup lang="ts">
import { computed } from 'vue'
import type { Note } from '../types'

const props = defineProps<{ notes: Note[] }>()
const emit = defineEmits<{ (e: 'clone', note: Note): void }>()

const ranked = computed(() =>
  [...props.notes].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 20)
)

function fmt(n: number | null | undefined): string {
  const v = n || 0
  return v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v)
}
</script>

<template>
  <div class="rank">
    <div v-for="(n, i) in ranked" :key="n.feed_id" class="rank-item" :class="{ hot: i === 0 }">
      <div class="score">{{ n.score?.toFixed(1) ?? '—' }}</div>
      <div class="bar" :style="{ background: n.cover_url ? '#9A6A2F' : '#9A6A2F' }"></div>
      <div class="info">
        <div class="t">{{ n.title || '（无标题）' }}</div>
        <div class="d">{{ n.nickname || '未知作者' }} · 点赞 {{ fmt(n.liked_count) }} · {{ i === 0 ? '最值得模仿' : '高评分候选' }}</div>
      </div>
      <div class="actions">
        <button class="b-btn primary" @click="emit('clone', n)">一键克隆</button>
        <button class="b-btn" @click="() => {}">采集客户</button>
      </div>
    </div>
  </div>
</template>
