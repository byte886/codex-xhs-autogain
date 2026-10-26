<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { appState, setStage, toast } from '../store'
import { apiCookieInfo, apiImportCookie, apiLoginStatus, apiNotes, apiSearch } from '../api'
import type { CookieItem } from '../api'

const emit = defineEmits<{ (e: 'collect-started'): void; (e: 'open-login'): void }>()

const showCookie = ref(false)
const importing = ref(false)
const cookieItems = ref<CookieItem[]>([])
const cookieInfo = ref({ count: 0, saved_at: '' })
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

const sortOptions = ['综合', '最新', '最热']
const typeOptions = ['全部', '图文', '视频']

async function doSearch() {
  if (appState.collecting) {
    // 停止
    appState.collecting = false
    appState.status = '已停止'
    return
  }
  appState.collecting = true
  appState.status = '搜索中'
  appState.selected = {}
  appState.taskLabel = `${appState.params.keyword} · ${appState.params.sort} · ${appState.params.noteType}`
  appState.searchNote = ''
  appState.elapsed = 0
  if (timer) clearInterval(timer)
  timer = setInterval(() => { appState.elapsed += 1 }, 1000)
  setStage('search')
  emit('collect-started')
  try {
    const r = await apiSearch(appState.params)
    if (timer) { clearInterval(timer); timer = null }
    if (r.note) {
      appState.searchNote = r.note
      toast(r.note)
    } else {
      toast(`采集完成：新增 ${r.added} 条，共 ${r.total} 条`)
    }
    // 拉取评分排序结果，自动推进到"筛选结果"阶段
    const list = await apiNotes('', 'score')
    appState.notes = list.notes
    appState.total = list.total
    appState.count = list.total
    appState.status = '展示中'
    setStage('pick')
  } catch (e) {
    if (timer) { clearInterval(timer); timer = null }
    toast(`采集失败：${(e as Error).message}`)
    appState.status = '失败'
  } finally {
    appState.collecting = false
  }
}

async function importCookie() {
  importing.value = true
  try {
    const r = await apiImportCookie()
    if (r.ok) {
      toast(`已从 Chrome 导入 Cookie（${r.count} 条），账号：${r.account || '已登录'}`)
      // 导入即校验：Chrome cookie 不一定能在容器生效（设备指纹不匹配）→ 无效则自动转扫码
      const lg = await apiLoginStatus()
      if (lg.logged_in) {
        appState.login = { logged_in: true, nickname: lg.nickname || '' }
      } else {
        toast('导入的 Cookie 在容器内未生效（设备指纹不匹配），请扫码登录')
        emit('open-login')
      }
    } else {
      toast(`导入失败：${r.error || '未知错误'}`)
    }
  } catch (e) {
    toast(`导入失败：${(e as Error).message}`)
  } finally {
    importing.value = false
    refreshCookieInfo()
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
          <input v-model="appState.params.keyword" placeholder="输入关键词，如：翡翠" @keyup.enter="doSearch" />
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
            <label>全局请求间隔（ms）</label>
            <input v-model.number="appState.params.interval" type="number" min="300" step="100" />
          </div>
        </div>
        <button class="btn-search" :class="{ stop: appState.collecting }" @click="doSearch">
          {{ appState.collecting ? '停止搜索' : '开始搜索' }}
        </button>
      </div>
    </section>

    <!-- Cookie -->
    <section>
      <div class="sec-title">Cookie</div>
      <div class="cookie-box">
        <div class="cookie-actions">
          <button class="c-btn icon" title="显示/隐藏 Cookie" @click="showCookie = !showCookie">
            {{ showCookie ? '▾' : '▸' }}
          </button>
          <button class="c-btn primary" :disabled="importing" @click="importCookie">
            {{ importing ? '导入中…' : '导入 Cookie（从浏览器）' }}
          </button>
          <button class="c-btn" @click="emit('open-login')">扫码登录（容器）</button>
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
        <label class="cookie-save">
          <input type="checkbox" checked /> 使用加密保存到本机，下次自动读取
        </label>
      </div>
    </section>
  </aside>
</template>
