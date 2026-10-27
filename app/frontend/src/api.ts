import type { ClonePayload, LoginStatus, LogItem, Note, SearchParams, SearchResult } from './types'

const BASE = ''

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) msg = String(body.detail)
    } catch { /* keep default */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export async function apiLoginStatus(): Promise<LoginStatus> {
  return j(await fetch(`${BASE}/api/login_status`))
}

export async function apiSearch(p: SearchParams): Promise<SearchResult> {
  const body = { keyword: p.keyword, limit: p.limit, sort: p.sort, note_type: p.noteType, interval: p.interval }
  return j(await fetch(`${BASE}/api/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }))
}

export async function apiNotes(keyword = '', order = 'score'): Promise<{ total: number; notes: Note[] }> {
  const q = new URLSearchParams()
  if (keyword) q.set('keyword', keyword)
  if (order) q.set('order', order)
  return j(await fetch(`${BASE}/api/notes?${q.toString()}`))
}

export async function apiClone(feedId: string): Promise<ClonePayload> {
  return j(await fetch(`${BASE}/api/clone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feed_id: feedId })
  }))
}

export async function apiPublish(payload: {
  title: string
  desc: string
  tags: string[]
  images: string[]
  visible: string
  account?: string
}): Promise<{ ok: boolean; note?: string; [k: string]: unknown }> {
  return j(await fetch(`${BASE}/api/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }))
}

export async function apiSaveDraft(payload: {
  title: string
  desc: string
  tags: string[]
  images: string[]
  visible: string
}): Promise<{ ok: boolean; note?: string; [k: string]: unknown }> {
  return j(await fetch(`${BASE}/api/save_draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }))
}

export async function apiLogs(): Promise<LogItem[]> {
  return j(await fetch(`${BASE}/api/logs`))
}

export async function apiImportCookie(): Promise<{ ok: boolean; count?: number; account?: string; error?: string }> {
  return j(await fetch(`${BASE}/api/import_cookie`, { method: 'POST' }))
}

export interface ImportCookieResult { ok: boolean; valid?: boolean; count?: number; account?: string; error?: string; note?: string; need_auth?: boolean }

export async function apiImportCookieText(text: string): Promise<ImportCookieResult> {
  return j(await fetch(`${BASE}/api/import_cookie`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cookie_text: text })
  }))
}

/** 方案 A：直接读日常 Chrome 的 Cookie 库（钥匙串解密），无需手动复制粘贴 */
export async function apiRefreshCookieFromChrome(): Promise<ImportCookieResult> {
  return j(await fetch(`${BASE}/api/refresh_cookie_from_chrome`, { method: 'POST' }))
}

export interface CookieItem { name: string; value: string; domain: string }

export async function apiCookieInfo(): Promise<{ ok: boolean; count: number; saved_at: string; cookies: CookieItem[] }> {
  return j(await fetch(`${BASE}/api/cookie_info`))
}

export async function apiQrcode(): Promise<{ ok: boolean; guide?: string; url?: string; expires_in?: number; error?: string }> {
  return j(await fetch(`${BASE}/api/qrcode`))
}
