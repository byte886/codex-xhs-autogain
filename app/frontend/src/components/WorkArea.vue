<script setup lang="ts">
import { ref } from 'vue'
import { appState, setStage } from '../store'
import type { Note, Author, ClientUser } from '../types'
import { apiAuthors, apiClientsList } from '../api'
import CardGrid from './CardGrid.vue'

const emit = defineEmits<{
  (e: 'menu', payload: { note: Note; x: number; y: number }): void
  (e: 'clone', note: Note): void
  (e: 'clients', note: Note): void
}>()

const filterText = ref('')
// P2 作者价值聚合 + P4/O1-3 客户库：笔记 / 作者榜 / 客户库 视图切换
const view = ref<'notes' | 'authors' | 'clients'>('notes')
const authors = ref<Author[]>([])
const authorsLoaded = ref(false)
const clients = ref<ClientUser[]>([])
const clientsLoaded = ref(false)
/** O1-3 意向筛选：'' 全部 / high 高意向 / 7d 近7天 */
const clIntent = ref('')
const clTab = ref<'all' | 'high' | '7d'>('all')

// O1-3 意向档位展示映射（与后端 intent.py 一致）
const INTENT_META: Record<string, { label: string; cls: string }> = {
  high: { label: '高意向', cls: 'i-high' },
  upper: { label: '上意向', cls: 'i-upper' },
  middle: { label: '中意向', cls: 'i-mid' },
  low: { label: '低意向', cls: 'i-low' },
}
const intentOf = (c: ClientUser) => c.intent || 'low'
const intentLabel = (c: ClientUser) => INTENT_META[intentOf(c)]?.label || '低意向'
const intentCls = (c: ClientUser) => INTENT_META[intentOf(c)]?.cls || 'i-low'

function fmtTime(s: number) {
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function cloneFromRank(n: Note) {
  emit('menu', { note: n, x: Math.max(window.innerWidth - 220, 200), y: 220 })
}

function fmtCount(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

function fmtPub(ts: number | null): string {
  if (ts == null) return '—'
  const d = new Date(ts > 1e12 ? ts : ts * 1000)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function showAuthors() {
  view.value = 'authors'
  if (!authorsLoaded.value) {
    try {
      const res = await apiAuthors()
      authors.value = res.authors || []
      authorsLoaded.value = true
    } catch {
      authors.value = []
    }
  }
}

function showNotes() {
  view.value = 'notes'
}

/** 点作者：切回笔记视图并按昵称过滤，只看该作者的笔记 */
function pickAuthor(a: Author) {
  view.value = 'notes'
  filterText.value = a.nickname || a.user_id
}

/** 客户库：按意向/时间筛选重取（all/high/7d） */
async function loadClients() {
  try {
    let intent = ''
    if (clTab.value === 'high') intent = 'high'
    const res = await apiClientsList('', intent)
    let list = res.clients || []
    if (clTab.value === '7d') {
      const weekAgo = Date.now() - 7 * 86400_000
      list = list.filter((c) => {
        const t = c.created_at ? new Date(c.created_at).getTime() : 0
        return t >= weekAgo
      })
    }
    clients.value = list
  } catch {
    clients.value = []
  }
}

async function showClients() {
  view.value = 'clients'
  if (!clientsLoaded.value) {
    await loadClients()
    clientsLoaded.value = true
  }
}

async function setClTab(tab: 'all' | 'high' | '7d') {
  clTab.value = tab
  await loadClients()
}

/** O1-3 统计：总数 / 高意向数 / 留联系方式命中 */
function clSummary() {
  let high = 0
  let contact = 0
  for (const c of clients.value) {
    if (c.intent === 'high') high += 1
    if (c.intent === 'high' && /微信|vx|v信|卫星|手机|电话|滴滴|私信|si me|si 我|si我|v /.test((c.matched_word || '') + (c.sample_comment || '').toLowerCase())) contact += 1
  }
  return { total: clients.value.length, high, contact }
}

/** O1-3 导出线索 CSV（昵称/主页链接/评论原文/命中词/IP属地/意向） */
function exportClientsCsv() {
  const head = ['昵称', '主页链接', '评论原文', '命中词', 'IP属地', '意向', '采集时间']
  const rows = clients.value.map((c) => [
    c.nickname || '', c.profile || '', (c.sample_comment || '').replace(/"/g, '""'),
    c.matched_word || '', c.ip_location || '', intentLabel(c), (c.created_at || '').slice(0, 10),
  ])
  const csv = '\ufeff' + [head, ...rows].map((r) => r.map((v) => `"${v}"`).join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  a.download = `线索导出_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

/** O1-3 去私信：入口占位（私信话术模块 S3 开发中，接通后跳话术视图） */
function goTalk(c: ClientUser) {
  // 打开用户主页 + 提示（话术模块在 S3 接通）
  if (c.profile) window.open(c.profile, '_blank')
  // eslint-disable-next-line no-alert
  alert('私信话术模块开发中（下一步接通）：将基于昵称/属地/意向/原评论生成话术，由你确认后发送。')
}

function openProfile(p: string | null) {
  if (p) window.open(p, '_blank')
}
</script>

<template>
  <div>
    <!-- 待输入 -->
    <div v-if="appState.stage === 'input'" class="empty">
      <div class="big">待输入</div>
      <p>在左侧填写关键词并设置参数，点击「开始采集」</p>
      <p class="sub">（视频示例："比如说关键词输入一个减肥"）</p>
    </div>

    <!-- 采集作品 -->
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
          <span class="k">已完成</span><span class="v">{{ appState.notes.length }}</span>
          <span class="v small">{{ appState.elapsed }}s</span>
        </div>
      </div>
      <CardGrid :notes="appState.notes" @menu="emit('menu', $event)" />
      <div v-if="appState.searchNote" style="margin-top:12px;font-size:12px;color:var(--ink-2);background:#FFF7E6;border-radius:8px;padding:8px 12px">
        {{ appState.searchNote }}
      </div>
    </div>

    <!-- 选择内容（一页式：评分徽标卡片 + 筛选 + 勾选 + 单条克隆；P2 新增作者榜视图） -->
    <div v-else-if="appState.stage === 'pick'">
      <div class="info-bar">
        <div class="info-item">
          <span class="k">评分模型</span><span class="v small">互动密度 × 内容结构 × 爆款潜力（卡片右上角分数）</span>
        </div>
        <div class="info-item">
          <span class="k">操作提示</span><span class="v small">点击卡片勾选；卡片「一键克隆」单条进入发布，发布手动确认</span>
        </div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
        <div class="view-switch">
          <button class="vs-btn" :class="{ on: view === 'notes' }" @click="showNotes">笔记 {{ appState.notes.length }}</button>
          <button class="vs-btn" :class="{ on: view === 'authors' }" @click="showAuthors">作者榜</button>
          <button class="vs-btn" :class="{ on: view === 'clients' }" @click="showClients">客户库</button>
        </div>
        <input v-model="filterText" placeholder="请选标题,作者或UID" style="flex:1;min-width:0" />
      </div>

      <!-- 作者榜（P2）：从当前采集集按作者聚合，低粉爆款多 × 均分高 = 值得模仿 -->
      <div v-if="view === 'authors'" class="author-grid">
        <div v-if="!authors.length" class="empty" style="padding:40px 0">
          <div class="big">暂无作者</div>
          <p>先采集笔记，作者榜会按博主聚合展示（低粉爆款越多的博主越值得模仿）</p>
        </div>
        <div v-for="a in authors" :key="a.user_id" class="author-card" @click="pickAuthor(a)">
          <div class="au-top">
            <img v-if="a.avatar" :src="a.avatar" alt="" class="au-avatar" referrerpolicy="no-referrer" />
            <div v-else class="au-avatar au-ph">{{ (a.nickname || '博')[0] }}</div>
            <div class="au-name">
              <div class="au-nick">{{ a.nickname || '未知作者' }}</div>
              <div class="au-sub">{{ fmtCount(a.fans_count) }} 粉丝</div>
            </div>
            <span v-if="a.hot_count > 0" class="au-hot">🔥{{ a.hot_count }} 低粉爆款</span>
          </div>
          <div class="au-stats">
            <div class="au-stat"><span class="k">收录</span><b>{{ a.note_count }}</b><span class="unit">篇</span></div>
            <div class="au-stat"><span class="k">均分</span><b>{{ a.avg_score ?? '—' }}</b></div>
            <div class="au-stat"><span class="k">最高</span><b>{{ a.max_score ?? '—' }}</b></div>
            <div class="au-stat"><span class="k">总赞</span><b>{{ fmtCount(a.total_liked) }}</b></div>
            <div class="au-stat"><span class="k">最新</span><b class="small">{{ fmtPub(a.last_publish) }}</b></div>
          </div>
          <div class="au-foot">点击查看该作者全部笔记 →</div>
        </div>
      </div>

      <!-- 客户库（O1-3）：评论区线索列表 —— 意向分级/高意向置顶/属地/命中词/去私信/导出 CSV -->
      <div v-else-if="view === 'clients'">
        <div v-if="!clients.length" class="empty" style="padding:40px 0">
          <div class="big">暂无客户</div>
          <p>在卡片上点「采集客户」抓取笔记评论区的互动用户，会在这里按意向分级展示（高意向置顶）</p>
        </div>
        <template v-else>
          <div class="info-bar">
            <div class="info-item"><span class="k">线索总数</span><span class="v">{{ clSummary().total }}</span></div>
            <div class="info-item"><span class="k">高意向</span><span class="v" style="color:#FF2442">{{ clSummary().high }}</span></div>
            <div class="info-item"><span class="k">留联系方式命中</span><span class="v" style="color:#00A86B">{{ clSummary().contact }}</span></div>
            <div class="info-item"><span class="k">采集说明</span><span class="v small">高意向置顶，其余按互动足迹排序；「去私信」仅生成话术，发送由你确认</span></div>
            <div style="margin-left:auto;display:flex;gap:8px">
              <button class="btn sm primary" @click="exportClientsCsv">导出线索 CSV</button>
            </div>
          </div>
          <div style="display:flex;gap:6px;margin-bottom:10px">
            <button class="cl-tab" :class="{ on: clTab === 'all' }" @click="setClTab('all')">全部 {{ clients.length }}</button>
            <button class="cl-tab" :class="{ on: clTab === 'high' }" @click="setClTab('high')">高意向 {{ clSummary().high }}</button>
            <button class="cl-tab" :class="{ on: clTab === '7d' }" @click="setClTab('7d')">近 7 天</button>
          </div>
          <div class="cl-list">
            <div v-for="c in clients" :key="c.uid" class="cl-row">
              <div class="cl-av au-ph">{{ (c.nickname || '客')[0] }}</div>
              <div class="cl-main">
                <div class="cl-top">
                  <span class="cl-nick">{{ c.nickname || '未知用户' }}</span>
                  <span class="cl-intent" :class="intentCls(c)">{{ intentLabel(c) }}</span>
                  <span v-if="c.matched_word" class="cl-word">命中「{{ c.matched_word }}」</span>
                  <span v-if="c.ip_location" class="cl-ip">📍 {{ c.ip_location }}</span>
                  <span v-if="c.comment_time" class="cl-time">{{ c.comment_time }}</span>
                  <a v-if="c.profile" :href="c.profile" target="_blank" class="cl-link" @click.stop>主页 ↗</a>
                </div>
                <div class="cl-cmt">“{{ (c.sample_comment || '—').slice(0, 90) }}”</div>
                <div class="cl-foot">足迹 {{ c.comment_count }} 次 · 关联 {{ c.note_count }} 篇 · 采集于 {{ (c.created_at || '').slice(0, 10) }}</div>
              </div>
              <div class="cl-act">
                <button class="btn sm" @click="goTalk(c)">去私信</button>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 笔记视图 -->
      <CardGrid v-else :notes="appState.notes" selectable :filter-text="filterText" @menu="emit('menu', $event)" @clone="emit('clone', $event)" @clients="emit('clients', $event)" />
    </div>
  </div>
</template>

<style scoped>
.view-switch {
  display: inline-flex;
  background: var(--bg-2, #f2f3f5);
  border-radius: 9px;
  padding: 3px;
  gap: 3px;
}
.vs-btn {
  border: 0;
  background: transparent;
  color: var(--ink-2, #888);
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.15s;
}
.vs-btn.on {
  background: #fff;
  color: var(--ink-1, #111);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
  font-weight: 600;
}
.author-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}
.author-card {
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 14px;
  padding: 14px;
  cursor: pointer;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.author-card:hover {
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
  border-color: #e0b36b;
}
.au-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.au-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}
.au-ph {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #ff6a3d, #ffb03d);
  color: #fff;
  font-weight: 700;
  font-size: 18px;
}
.au-nick {
  font-weight: 600;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.au-sub {
  font-size: 12px;
  color: var(--ink-2, #888);
  margin-top: 2px;
}
.au-hot {
  margin-left: auto;
  flex-shrink: 0;
  background: #fff1e6;
  color: #d24a2d;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 8px;
}
.au-stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
  background: var(--bg-2, #f7f7f8);
  border-radius: 10px;
  padding: 10px 8px;
}
.au-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.au-stat .k {
  font-size: 11px;
  color: var(--ink-2, #999);
}
.au-stat b {
  font-size: 15px;
}
.au-stat b.small {
  font-size: 12px;
}
.au-stat .unit {
  font-size: 11px;
  color: var(--ink-2, #999);
}
.au-foot {
  margin-top: 10px;
  font-size: 12px;
  color: #b08a4d;
  text-align: right;
}
.au-cmt {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ink-2, #666);
  background: var(--bg-2, #f7f7f8);
  border-radius: 8px;
  padding: 8px 10px;
  line-height: 1.5;
}
/* ===== O1-3 客户库：评论区线索列表 ===== */
.cl-tab {
  border: 1px solid var(--line, #ececec);
  background: var(--bg-card, #fff);
  color: var(--ink-2, #888);
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.cl-tab.on {
  border-color: #e0b36b;
  color: #b08a4d;
  font-weight: 600;
  background: #fff8ef;
}
.cl-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cl-row {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--line, #ececec);
  border-radius: 12px;
  padding: 12px 14px;
}
.cl-row:hover {
  border-color: #e0b36b;
}
.cl-av {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
  font-size: 16px;
}
.cl-main {
  flex: 1;
  min-width: 0;
}
.cl-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cl-nick {
  font-weight: 600;
  font-size: 14px;
}
.cl-intent {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 6px;
  color: #fff;
}
.cl-intent.i-high { background: #FF2442; }
.cl-intent.i-upper { background: #FF9D5C; }
.cl-intent.i-mid { background: #F79009; }
.cl-intent.i-low { background: #9AA5B1; }
.cl-word {
  font-size: 11px;
  color: #b08a4d;
  background: #fff8ef;
  border-radius: 6px;
  padding: 2px 6px;
}
.cl-ip {
  font-size: 11px;
  color: var(--ink-2, #888);
}
.cl-time {
  font-size: 11px;
  color: var(--ink-2, #999);
}
.cl-link {
  margin-left: auto;
  font-size: 12px;
  color: #b08a4d;
  text-decoration: none;
}
.cl-cmt {
  margin-top: 5px;
  font-size: 12px;
  color: var(--ink-2, #666);
  line-height: 1.5;
}
.cl-foot {
  margin-top: 4px;
  font-size: 11px;
  color: var(--ink-2, #999);
}
.cl-act {
  flex-shrink: 0;
}
</style>
