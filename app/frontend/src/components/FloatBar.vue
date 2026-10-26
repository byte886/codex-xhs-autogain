<script setup lang="ts">
import { computed } from 'vue'
import { appState, selectedCount, selectedNotes } from '../store'
import { toast } from '../store'

const emit = defineEmits<{
  (e: 'clone-all'): void
  (e: 'collect-clients'): void
}>()

const n = computed(() => selectedCount())
const has = computed(() => selectedNotes().length > 0)

function clone() {
  if (!has.value) { toast('请先勾选要克隆的笔记'); return }
  emit('clone-all')
}

function clients() {
  if (!has.value) { toast('请先勾选要采集客户的笔记'); return }
  emit('collect-clients')
}
</script>

<template>
  <div class="float-bar">
    <div class="hint">对勾选的 {{ n }} 条笔记批量执行</div>
    <button class="f-btn clone" @click="clone">一键克隆 <span class="n">{{ n }}</span></button>
    <button class="f-btn client" @click="clients">一键采集客户 <span class="n">{{ n }}</span></button>
  </div>
</template>
