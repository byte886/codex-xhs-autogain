export type Stage = 'input' | 'search' | 'filter' | 'pick'

export interface Note {
  feed_id: string
  keyword: string | null
  title: string
  note_type: string
  user_id: string | null
  nickname: string | null
  avatar: string | null
  cover_url: string | null
  xsec_token: string | null
  desc: string | null
  ip_location: string | null
  liked_count: number | null
  collected_count: number | null
  comment_count: number | null
  shared_count: number | null
  fans_count: number | null
  publish_time: string | null
  score: number | null
  tag: string | null
  fan_ratio: number | null
  fetched_at: string | null
  /** 前端临时字段：是否被选中 */
  selected?: boolean
  /** 前端临时字段：展示用本地图片 */
  local_images?: string[]
}

export interface SearchParams {
  keyword: string
  limit: number
  sort: string
  noteType: string
  interval: number
}

export interface SearchResult {
  added: number
  total: number
  top: Array<{ feed_id: string; title: string; score: number }>
  note?: string
}

export interface ClonePayload {
  feed_id: string
  title: string
  desc: string
  images: string[]
  detail_ok: boolean
}

export interface LoginStatus {
  logged_in: boolean
  nickname?: string
  [k: string]: unknown
}

export interface LogItem {
  [k: string]: unknown
}
