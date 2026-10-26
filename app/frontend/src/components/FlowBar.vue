<script setup lang="ts">
import { computed } from 'vue'
import { appState, stageName, stageOrder } from '../store'

const steps = [
  { s: 'input', label: '待输入' },
  { s: 'search', label: '搜索作品' },
  { s: 'filter', label: '筛选结果' },
  { s: 'pick', label: '选择内容' }
] as const

const cur = computed(() => appState.stage)
const hint = computed(() => stageName[appState.stage])
</script>

<template>
  <div class="flow">
    <template v-for="(st, i) in steps" :key="st.s">
      <span v-if="i > 0" class="arrow">→</span>
      <span
        class="step"
        :class="{ cur: cur === st.s, done: stageOrder.indexOf(cur) > stageOrder.indexOf(st.s) }"
      >
        <span class="dot">{{ i + 1 }}</span>{{ st.label }}
      </span>
    </template>
    <div class="flow-hint">当前：<b>{{ hint }}</b></div>
  </div>
</template>
