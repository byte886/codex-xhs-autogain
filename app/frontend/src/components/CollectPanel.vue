<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { appState, setStage, toast } from '../store'
import { apiCollectProgress, apiCollectStart, apiCollectStop, apiCookieInfo, apiImportCookieText, apiLoginStatus, apiNotes } from '../api'
import type { CookieItem, ImportCookieResult } from '../api'

const emit = defineEmits<{ (e: 'collect-started'): void; (e: 'open-login'): void }>()

const showCookie = ref(false)
const cookieItems = ref<CookieItem[]>([])
const cookieInfo = ref({ count: 0, saved_at: '' })
const showImport = ref(false)
const importText = ref('')
const importing = ref(false)
const importResult = ref<ImportCookieResult | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function refreshCookieInfo() {
  try {
    const r = await apiCookieInfo()
    if (r.ok) {
      cookieItems.value = r.cookies
      cookieInfo.value = { count: r.count, saved_at: r.saved_at }
    }
  } catch (e) {
    /* 后端未就绪时静默 */
  }
}

function cookieText(): string {
  return cookieItems.value.map(c => `${c.name}=${c.value}`).join('\n')
}

const collectOptions = [
  { value: 'kw', label: '关键词搜索笔记', enabled: true },
  { value: 'blogger', label: '博主/对标账号采集（即将上线）', enabled: false },
  { value: 'comment', label: '评论采集（即将上线）', enabled: false },
  { value: 'link', label: '指定链接采集（即将上线）', enabled: false },
  { value: 'wm', label: '去水印下载（即将上线）', enabled: false },
  { value: 'hot', label: '热门/赛道采集（即将上线）', enabled: false },
  { value: 'user', label: '互动用户采集（即将上线）', enabled: false },
  { value: 'timer', label: '定时/计划采集（即将上线）', enabled: false }
]

const sortOptions = ['综合', '最新', '最多点赞', '最多评论', '最多收藏']
const typeOptions = ['全部', '图文', '视频']
const timeOptions = ['不限', '一天内', '一周内', '半年内']
const scopeOptions = ['不限', '已看过', '未看过', '已关注']
const distanceOptions = ['不限', '同城', '附近']

let collectTaskId = ''
let collectTimer: ReturnType<typeof setInterval> | null = null

// 任务式采集：开始采集 → 后台逐批提取（进度轮询）→ 采满/平台不增长/用户停止后统一评分排序展示
async function doCollect() {
  if (appState.collecting) {
    // 采集中再点 = 停止采集
    if (collectTaskId) {
      try { await apiCollectStop(collectTaskId) } catch { /* 忽略 */ }
      toast('已请求停止，正在收尾并排序…')
    }
    return
  }
  if (!appState.params.keyword.trim()) { toast('请先输入关键词'); return }
  appState.collecting = true
  appState.status = '采集中'
  appState.selected = {}
  appState.taskLabel = `${appState.params.keyword} · ${appState.params.sort} · ${appState.params.noteType}`
  appState.searchNote = ''
  appState.elapsed = 0
  if (timer) clearInterval(timer)
  timer = setInterval(() => { appState.elapsed += 1 }, 1000)
  setStage('search')
  emit('collect-started')
  try {
    const r = await apiCollectStart(appState.params)
    collectTaskId = r.task_id
    appState.searchNote = `采集中 0/${r.limit}…（可随时点「停止采集」）`
    collectTimer = setInterval(async () => {
      try {
        const pr = await apiCollectProgress(collectTaskId)
        if (pr.status === 'running') {
          appState.searchNote = `采集中 ${pr.collected}/${pr.limit}…（可随时点「停止采集」）`
          return
        }
        // 完成 / 停止 / 失败：统一收尾
        if (collectTimer) { clearInterval(collectTimer); collectTimer = null }
        if (timer) { clearInterval(timer); timer = null }
        appState.collecting = false
        if (pr.status === 'error') {
          appState.searchNote = pr.note || '采集失败'
          appState.status = '失败'
          toast(pr.note || '采集失败')
          if ((pr.note || '').includes('登录态')) emit('open-login')
          return
        }
        appState.searchNote = pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成')
        toast(pr.note || (pr.status === 'stopped' ? '已停止采集' : '采集完成'))
        // 采集完成后拉评分排序结果，自动推进到"筛选结果"阶段
        const list = await apiNotes('', 'score')
        appState.notes = list.notes
        appState.total = list.total
        appState.count = list.total
        appState.status = '展示中'
        setStage('pick')
      } catch (e2) {
        if (collectTimer) { clearInterval(collectTimer); collectTimer = null }
        if (timer) { clearInterval(timer); timer = null }
        appState.collecting = false
        toast(`查询进度失败：${(e2 as Error).message}`)
      }
    }, 1000)
  } catch (e) {
    if (timer) { clearInterval(timer); timer = null }
    const msg = (e as Error).message
    toast(`启动采集失败：${msg}`)
    appState.status = '失败'
    if (msg.includes('登录态')) emit('open-login')
    appState.collecting = false
  }
}

// 导入 Cookie：粘贴文本 → 后端解析并注入独立浏览器校验有效性 → 展示结果
async function doImportCookie() {
  if (importing.value) return
  const text = importText.value.trim()
  if (!text) { toast('请先粘贴 Cookie 文本（name=value; 格式）'); return }
  importing.value = true
  importResult.value = null
  try {
    const r = await apiImportCookieText(text)
    importResult.value = r
    if (r.ok) {
      toast(`Cookie 有效：${r.account || '已登录'}（${r.count} 条）已保存`)
      refreshCookieInfo()
    } else {
      toast(r.error || 'Cookie 无效')
      // 无效 → 引导扫码登录兜底
      emit('open-login')
    }
  } catch (e) {
    const msg = (e as Error).message
    importResult.value = { ok: false, error: msg }
    toast(`导入失败：${msg}`)
  } finally {
    importing.value = false
  }
}


// 扫码登录成功后容器 cookies.json 已更新 → 自动刷新 Cookie 显示（登录态/Cookie 三方联动）
watch(() => appState.cookieRefreshTick, () => refreshCookieInfo())

onMounted(async () => {
  refreshCookieInfo()
  try {
    const lg = await apiLoginStatus()
    appState.login = { logged_in: !!lg.logged_in, nickname: lg.nickname || '' }
  } catch { /* 后端未就绪时静默 */ }
})
</script>

<template>
  <aside class="panel">
    <!-- 采集功能 -->
    <section>
      <div class="sec-title">采集功能 <span class="tag">MVP</span></div>
      <div class="param">
        <label>采集方式</label>
        <select style="width:100%" @change="() => toast('演示：其余采集方式即将上线')">
          <option v-for="o in collectOptions" :key="o.value" :value="o.value" :disabled="!o.enabled" :selected="o.value === 'kw'">
            {{ o.label }}
          </option>
        </select>
      </div>
    </section>

    <!-- 搜索参数 -->
    <section>
      <div class="sec-title">搜索参数</div>
      <div class="param-grid">
        <div class="param">
          <label>关键词</label>
          <input v-model="appState.params.keyword" placeholder="输入关键词，如：翡翠" @keyup.enter="doCollect" />
        </div>
        <div class="param-row">
          <div class="param">
            <label>结果上限</label>
            <input v-model.number="appState.params.limit" type="number" min="1" max="200" />
          </div>
          <div class="param">
            <label>搜索排序</label>
            <select v-model="appState.params.sort" style="width:100%">
              <option v-for="s in sortOptions" :key="s">{{ s }}</option>
            </select>
          </div>
        </div>
        <div class="param-row">
          <div class="param">
            <label>笔记类型</label>
            <select v-model="appState.params.noteType" style="width:100%">
              <option v-for="t in typeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
          <div class="param">
            <label>发布时间</label>
            <select v-model="appState.params.timeRange" style="width:100%">
              <option v-for="t in timeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
        </div>
        <div class="param-row">
          <div class="param">
            <label>搜索范围</label>
            <select v-model="appState.params.scope" style="width:100%">
              <option v-for="t in scopeOptions" :key="t">{{ t }}</option>
            </select>
          </div>
          <div class="param">
            <label>位置距离</label>
            <select v-model="appState.params.distance" style="width:100%">
              <option v-for="t in distanceOptions" :key="t">{{ t }}</option>
            </select>
          </div>
        </div>
        <div class="param-row">
          <div class="param">
            <label>全局请求间隔（ms）</label>
            <input v-model.number="appState.params.interval" type="number" min="300" step="100" />
          </div>
        </div>
        <button class="btn-search" :class="{ stop: appState.collecting }" @click="doCollect">
          <span v-if="appState.collecting" class="btn-spinner" />
          {{ appState.collecting ? '停止采集' : '开始采集' }}
        </button>
      </div>
    </section>

    <!-- Cookie -->
    <section>
      <div class="sec-title">Cookie</div>
      <div class="cookie-box">
        <div class="cookie-status" :class="appState.login?.logged_in ? 'ok' : 'err'">
          {{ appState.login?.logged_in ? `● 已登录（${appState.login.nickname || '小红书'}），Cookie 有效` : '● 未登录 / Cookie 失效，请登录或导入 Cookie' }}
        </div>
        <div class="cookie-actions">
          <button class="c-btn icon" title="显示/隐藏 Cookie" @click="showCookie = !showCookie">
            {{ showCookie ? '▾' : '▸' }}
          </button>
          <button class="c-btn" @click="emit('open-login')">去 Chrome 扫码登录</button>
          <button class="c-btn" @click="showImport = !showImport">
            {{ showImport ? '收起导入' : '导入 Cookie' }}
          </button>
        </div>
        <div class="cookie-val" :class="{ show: showCookie }">
          <textarea
            v-if="showCookie"
            readonly
            :value="cookieText()"
            spellcheck="false"
            placeholder="加载中…"
          ></textarea>
          <span v-else class="secret">
            {{ cookieInfo.count ? `${cookieInfo.count} 条 cookie（点击「显示 Cookie」查看）` : 'Cookie 信息未加载' }}
          </span>
        </div>
        <!-- 导入 Cookie 面板：粘贴 → 校验 → 结果 -->
        <div v-if="showImport" class="cookie-import">
          <textarea
            v-model="importText"
            spellcheck="false"
            placeholder="粘贴 Cookie 文本：web_session=xxx; a1=yyy; …（或每行一条 name=value）"
          ></textarea>
          <div class="cookie-import-actions">
            <button class="c-btn" @click="doImportCookie" :disabled="importing">
              {{ importing ? '校验中…' : '校验并导入' }}
            </button>
            <button class="c-btn" @click="emit('open-login')">去 Chrome 扫码登录</button>
          </div>
          <p v-if="importResult" class="import-result" :class="{ err: !importResult.ok }">
            {{ importResult.ok ? `✔ 有效（${importResult.account || '已登录'}，${importResult.count} 条已保存）` : `✘ ${importResult.error}` }}
          </p>
          <p class="import-hint">
            导入的 Cookie 将在独立浏览器（生产 APP 环境）中校验并启用，不污染当前 Chrome 日常登录态。
            失效时搜索/克隆/采集会被拦截，请在此导入新 Cookie 或扫码登录。
          </p>
        </div>
        <label class="cookie-save">
          <input type="checkbox" checked /> 使用加密保存到本机，下次自动读取
        </label>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.cookie-status {
  font-size: 12px;
  line-height: 1.5;
  padding: 6px 10px;
  border-radius: 6px;
  margin-bottom: 8px;
  border: 1px solid transparent;
}
.cookie-status.ok { color: #16a34a; background: #f0fdf4; border-color: #bbf7d0; }
.cookie-status.err { color: #c0392b; background: #fef2f2; border-color: #fecaca; }
/* 搜索中按钮：转圈 + 禁用（淘宝式：点了等结果，无"停止"） */
.btn-search:disabled {
  opacity: 0.7;
  cursor: default;
}
.btn-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-right: 6px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: btn-spin 0.8s linear infinite;
  vertical-align: -1px;
}
@keyframes btn-spin {
  to { transform: rotate(360deg); }
}
</style>
