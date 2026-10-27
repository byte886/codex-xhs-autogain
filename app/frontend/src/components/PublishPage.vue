<script setup lang="ts">
import { computed, ref } from 'vue'
import { appState, coverProxy, setStage, toast } from '../store'
import { apiClone, apiPublish, apiSaveDraft, apiUploadImage, apiDrafts, apiDraftDelete } from '../api'
import type { DraftItem } from '../api'
import type { Note } from '../types'

const emit = defineEmits<{ (e: 'back'): void }>()

const title = ref('')
const body = ref('')
const tags = ref('')
const visible = ref('公开可见')
const account = ref('')
const images = ref<string[]>([])
const feedId = ref('')
const publishing = ref(false)
const loading = ref(false)

// 草稿库（简约版：列表 / 恢复续编 / 删除）
const drafts = ref<DraftItem[]>([])
const draftPanelOpen = ref(false)
const draftLoading = ref(false)

async function loadDrafts() {
  draftLoading.value = true
  try {
    const r = await apiDrafts()
    drafts.value = r.ok ? r.drafts : []
  } catch {
    drafts.value = []
  } finally {
    draftLoading.value = false
  }
}

function openDraftPanel() {
  draftPanelOpen.value = true
  loadDrafts()
}

function restoreDraft(d: DraftItem) {
  title.value = d.title || ''
  body.value = d.desc || ''
  tags.value = d.tags.join(' ')
  visible.value = d.visibility || '公开可见'
  images.value = [...d.images]
  feedId.value = d.feed_id || ''
  draftPanelOpen.value = false
  toast('已恢复草稿，可继续编辑后发布或重新保存')
}

async function removeDraft(id: number) {
  const r = await apiDraftDelete(id).catch(() => ({ ok: false }))
  toast(r.ok ? '草稿已删除' : '删除失败')
  loadDrafts()
}

const tCount = computed(() => title.value.length)
const bCount = computed(() => body.value.length)

type CloneTarget = Note | { feed_id: string; title: string; desc: string; images: string[]; originUrl?: string }

async function openFor(note: CloneTarget) {
  loading.value = true
  feedId.value = note.feed_id || ''
  try {
    if ('images' in note && note.images) {
      // 已有克隆载荷：直接填充
      title.value = note.title || ''
      body.value = note.desc || ''
      images.value = note.images
    } else {
      // 秒开：先用列表已有数据（标题/正文/封面占位），后台拉克隆载荷补图，不阻塞编辑
      title.value = note.title || ''
      body.value = (note as Note).desc || ''
      const localCover = (note as Note).local_cover || (note as Note).local_images?.[0] || ''
      images.value = localCover ? [coverProxy(localCover)] : []
      loading.value = false
      apiClone(note.feed_id)
        .then(p => {
          const locals = (p.images_local || []).filter(Boolean)
          if (locals.length) images.value = locals
          else if (p.images?.length) images.value = p.images
          if (p.title) title.value = p.title
          if (p.desc) body.value = p.desc
        })
        .catch(() => {
          /* 后台补图失败则保留占位封面，不打断编辑 */
        })
      return
    }
    tags.value = ''
    visible.value = '公开可见'
    account.value = ''
  } catch (e) {
    toast(`克隆失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

defineExpose({ openFor })

function removeImg(i: number) {
  images.value.splice(i, 1)
}

// 拖拽排序（原生 HTML5 DnD）：拖放后重排 images 数组，发布/草稿按新顺序生效
const dragIndex = ref(-1)
function onDragStart(i: number) {
  dragIndex.value = i
}
function onDragOver(ev: DragEvent) {
  ev.preventDefault()
}
function onDrop(i: number) {
  if (dragIndex.value >= 0 && dragIndex.value !== i) {
    const [moved] = images.value.splice(dragIndex.value, 1)
    images.value.splice(i, 0, moved)
  }
  dragIndex.value = -1
}
function onDragEnd() {
  dragIndex.value = -1
}

// 图片全图预览（lightbox）
const previewUrl = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const appending = ref(false)

function openPreview(url: string) {
  previewUrl.value = url
}

function triggerAdd() {
  fileInput.value?.click()
}

async function onAddImages(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = [...(input.files || [])]
  input.value = '' // 允许重复选同一文件
  if (!files.length) return
  appending.value = true
  let added = 0
  for (const f of files) {
    const r = await apiUploadImage(f).catch(() => ({ ok: false, note: '上传失败', path: undefined }))
    if (r.ok && r.path) {
      images.value.push(r.path)
      added++
    } else {
      toast(`追加失败：${r.note || '未知原因'}`)
    }
  }
  appending.value = false
  if (added) toast(`已追加 ${added} 张图片（共 ${images.value.length} 张）`)
}

function back() {
  setStage('pick')
  emit('back')
}

async function saveDraft() {
  try {
    const r = await apiSaveDraft({
      title: title.value,
      desc: body.value,
      tags: tags.value.split(/[\s,，]+/).filter(Boolean).map(t => (t.startsWith('#') ? t : '#' + t)),
      images: images.value,
      visible: visible.value,
      feed_id: feedId.value
    })
    toast(r.ok ? r.note || '草稿已保存（长期保留）' : `保存失败：${r.note || '未知原因'}`)
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`)
  }
}

async function publish() {
  publishing.value = true
  try {
    // 前置检查：后端发布前校验账号与上传通道（现有护栏）
    const r = await apiPublish({
      title: title.value,
      desc: body.value,
      tags: tags.value.split(/[\s,，]+/).filter(Boolean).map(t => (t.startsWith('#') ? t : '#' + t)),
      images: images.value,
      visible: visible.value
    })
    toast(r.ok ? '发布成功' : `发布失败：${r.note || '未知原因'}`)
  } catch (e) {
    toast(`发布失败：${(e as Error).message}`)
  } finally {
    publishing.value = false
  }
}
</script>

<template>
  <div>
    <div style="background:var(--panel);border-bottom:1px solid var(--line);padding:12px 26px;font-size:13px;color:var(--ink-2)">
      发布编辑页 ·
      <a
        href="#"
        style="color:var(--red)"
        @click.prevent="toast('已复制原作品链接')"
      >复制原作品链接</a>
    </div>
    <div class="publish" style="margin-top:22px">
      <h2>发布笔记</h2>
      <div v-if="loading" style="text-align:center;padding:30px;color:var(--ink-2)">正在拉取笔记内容…</div>
      <template v-else>
        <div class="pub-field">
          <label>标题 <span class="limit">{{ tCount }}/50</span></label>
          <input v-model="title" maxlength="50" style="width:100%" />
        </div>
        <div class="pub-field">
          <label>正文 <span class="limit">{{ bCount }}/1000</span></label>
          <textarea v-model="body" rows="5" maxlength="1000" style="width:100%;resize:vertical"></textarea>
        </div>
        <div class="pub-field">
          <label>话题标签 <span class="limit">空格分隔</span></label>
          <input v-model="tags" placeholder="#翡翠 #手镯 #珠宝" style="width:100%" />
        </div>
        <div class="pub-field">
          <label>图片 <span class="limit">拖拽调序 / 点击看全图 / 删改 / 追加</span></label>
          <div class="pub-imgs">
            <div
              v-for="(img, i) in images"
              :key="i"
              class="pub-img"
              :class="{ dragging: dragIndex === i }"
              draggable="true"
              @dragstart="onDragStart(i)"
              @dragover="onDragOver"
              @drop="onDrop(i)"
              @dragend="onDragEnd"
            >
              <img v-if="img" :src="coverProxy(img)" alt="" @click="openPreview(coverProxy(img))" />
              <span class="x" @click="removeImg(i)">×</span>
            </div>
            <div class="pub-img add" :class="{ busy: appending }" @click="triggerAdd">{{ appending ? '…' : '＋' }}</div>
          </div>
          <input ref="fileInput" type="file" accept="image/*" multiple style="display:none" @change="onAddImages" />
        </div>
        <div class="pub-row">
          <div class="pub-field">
            <label>可见范围</label>
            <select v-model="visible" style="width:100%">
              <option>公开可见</option><option>仅粉丝可见</option><option>仅自己可见</option>
            </select>
          </div>
          <div class="pub-field">
            <label>发布账号</label>
            <select v-model="account" style="width:100%">
              <option value="">{{ appState.login.nickname || '当前账号' }}（当前）</option>
              <option disabled>账号 2（未配置）</option>
              <option disabled>账号 3（未配置）</option>
            </select>
          </div>
        </div>
        <div class="check-note">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#888" /><path d="M12 8v5M12 16.5v.01" stroke="#888" stroke-width="2" /></svg>
          发布前会自动检查账号和上传通道（视频原话）
        </div>
        <div class="pub-btns">
          <button class="btn-back" @click="back">返回作品列表</button>
          <button class="btn-draft" @click="saveDraft">保存草稿</button>
          <button class="btn-draft" @click="openDraftPanel">草稿库</button>
          <button class="btn-pub" :disabled="publishing" @click="publish">{{ publishing ? '发布中…' : '发布至' }}</button>
        </div>
      </template>
    </div>

    <!-- 草稿库弹窗（简约版：列表 / 点击恢复续编 / × 删除） -->
    <div v-if="draftPanelOpen" class="draft-overlay" @click.self="draftPanelOpen = false">
      <div class="draft-panel">
        <div class="draft-head">
          <b>草稿库</b>
          <span class="draft-close" @click="draftPanelOpen = false">×</span>
        </div>
        <div v-if="draftLoading" class="draft-empty">加载中…</div>
        <div v-else-if="!drafts.length" class="draft-empty">
          暂无草稿<br /><span class="dim">发布页编辑后点「保存草稿」即可保存（长期保留）</span>
        </div>
        <div v-else class="draft-list">
          <div v-for="d in drafts" :key="d.id" class="draft-item" @click="restoreDraft(d)">
            <img v-if="d.images[0]" class="draft-thumb" :src="coverProxy(d.images[0])" alt="" @error="($event.target as HTMLImageElement).style.display = 'none'" />
            <span v-else class="draft-thumb draft-thumb-empty">无图</span>
            <div class="draft-meta">
              <div class="draft-title">{{ d.title || '（无标题）' }}</div>
              <div class="draft-sub">{{ d.created_at?.replace('T', ' ') }} · {{ d.visibility }} · {{ d.images.length }} 图</div>
            </div>
            <button class="draft-del" @click.stop="removeDraft(d.id)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 图片全图预览（lightbox：点击遮罩/图关闭） -->
    <div v-if="previewUrl" class="img-lightbox" @click="previewUrl = ''">
      <img :src="previewUrl" alt="" @click.stop="previewUrl = ''" />
      <div class="lb-hint">点击任意处关闭</div>
    </div>
  </div>
</template>
