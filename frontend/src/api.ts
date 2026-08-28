import axios from 'axios'

export const http = axios.create({ baseURL: '/api', timeout: 60000 })

// ---------- 类型 ----------
export interface Station {
  id: number
  code: string
  name: string
  aliases: string[]
}

export interface BatchSummary {
  id: string
  start_date: string
  end_date: string
  handover_date: string
  status: string
  stations: string[]
  item_total: number
  pending_review: number
  created_at: string
}

export interface HandoverItemView {
  id: string
  work_item_id: string
  title: string
  status: string
  priority: string
  summary: string
  latest_progress: string
  blocker: string
  next_action: string
  previous_owner: string
  next_owner: string
  start_date: string | null
  end_date: string | null
  review_status: string
  human_edited: boolean
  revision: number
  source_ids: string[]
  color: string
  section: 'important' | 'handover'
}

export interface GeneralItemView {
  id: string
  plan_id: string
  library_id: string
  title: string
  category: string
  plan_start: string | null
  plan_end: string | null
  status: string
  owner: string
  note: string
  revision: number
  overdue: boolean
  color: string
  template_meta: {
    schedule: string
    doc_list: string
    doc_dir: string
    content: string
    reviewer: string
    remark: string
  } | null
}

export interface Staff {
  id: string
  station_code: string
  name: string
  role: string
  note: string
  is_active: boolean
}

export interface StationDetail {
  station_meta_id: string
  station_id: number
  station_code: string
  station_name: string
  duty_leader: string
  temp_leader: string
  operators: string[]
  items: HandoverItemView[]
  general: { monthly: GeneralItemView[]; quarterly: GeneralItemView[]; yearly: GeneralItemView[] }
  device_changes: { id: string; content: string }[]
  snapshots: { id: string; version: number; status: string; created_at: string; docx_path: string }[]
}

export interface BatchDetail {
  id: string
  start_date: string
  end_date: string
  handover_date: string
  status: string
  created_at: string
  stations: StationDetail[]
}

export interface SourceRow {
  date: string
  text: string
  sheet: string
  row_no: number | null
  status_hint: string
}

export interface ImportResult {
  status: 'success' | 'failed'
  job_id?: string
  inserted?: number
  skipped_duplicate?: number
  date_unresolved?: Array<{ sheet: string; row: number; date: string }>
  error?: string
}

export interface RenderResult {
  snapshot_id: string
  version: number
  sha256: string
  docx_path: string
  current_path: string
  cloud_path: string | null
  download_url: string
}

// ---------- API ----------
export const api = {
  stations: () => http.get<Station[]>('/stations').then(r => r.data),
  listBatches: () => http.get<BatchSummary[]>('/handovers').then(r => r.data),
  createBatch: (body: {
    start_date: string
    end_date: string
    handover_date: string
    station_ids: number[]
    meta_overrides?: Record<string, unknown>
  }) => http.post<{ id: string; status: string }>('/handovers', body).then(r => r.data),
  batchDetail: (id: string) => http.get<BatchDetail>(`/handovers/${id}`).then(r => r.data),
  patchItem: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.patch(`/handover-items/${id}`, { revision, ...fields }).then(r => r.data),
  reviewItem: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.post(`/handover-items/${id}/review`, { revision, ...fields }).then(r => r.data),
  approveItem: (id: string, revision: number) =>
    http.post(`/handover-items/${id}/approve`, { revision }).then(r => r.data),
  approveAll: (batchId: string, stationMetaId: string) =>
    http.post<{ approved: number }>(`/handovers/${batchId}/approve-all`, {
      station_meta_id: stationMetaId
    }).then(r => r.data),
  patchMeta: (metaId: string, fields: Record<string, unknown>) =>
    http.patch(`/handover-station-meta/${metaId}`, fields).then(r => r.data),
  addDeviceChange: (batchId: string, stationMetaId: string, content: string) =>
    http.post(`/handovers/${batchId}/device-changes`, {
      station_meta_id: stationMetaId,
      content
    }).then(r => r.data),
  itemSources: (workItemId: string) =>
    http.get<SourceRow[]>(`/work-items/${workItemId}/sources`).then(r => r.data),
  staff: (stationCode?: string) =>
    http.get<Staff[]>('/staff', { params: stationCode ? { station_code: stationCode } : {} })
      .then(r => r.data),
  patchGeneralItem: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.patch(`/general-items/${id}`, { revision, ...fields }).then(r => r.data),
  render: (batchId: string, stationMetaId: string) =>
    http
      .post<RenderResult>(`/handovers/${batchId}/render`, { station_meta_id: stationMetaId })
      .then(r => r.data),
  downloadUrl: (snapshotId: string) => `/api/documents/${snapshotId}/download`,
  importMeeting: (file: File, options: { defaultYear: number; stationCode?: string }) => {
    const body = new FormData()
    body.append('file', file)
    body.append('default_year', String(options.defaultYear))
    if (options.stationCode) body.append('station_code', options.stationCode)
    return http.post<ImportResult>('/imports/xlsx', body, { timeout: 120000 }).then(r => r.data)
  },
  importPlan: (file: File, options: {
    planMonth: string
    category: string
    defaultYear: number
    stationCode?: string
  }) => {
    const body = new FormData()
    body.append('file', file)
    body.append('plan_month', options.planMonth)
    body.append('category', options.category)
    body.append('default_year', String(options.defaultYear))
    if (options.stationCode) body.append('station_code', options.stationCode)
    return http.post<ImportResult>('/imports/monthly-plan', body, { timeout: 120000 })
      .then(r => r.data)
  }
}

// ---------- 展示辅助 ----------
export function cnDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-')
  return `${Number(y)}.${Number(m)}.${Number(d)}`
}

export function cnDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return iso
  const parts = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).formatToParts(value)
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find(item => item.type === type)?.value || ''
  return `${part('year')}.${part('month')}.${part('day')} ${part('hour')}:${part('minute')}`
}

export const STATUS_LABEL: Record<string, string> = {
  pending: '待复核',
  approved: '已确认',
  edited: '已编辑',
  rejected: '已退回'
}

export const PRIORITY_LABEL: Record<string, string> = {
  urgent: '紧急',
  important: '重点',
  normal: '普通'
}

export const COLOR_HEX: Record<string, string> = {
  red: '#FFA5A5',
  yellow: '#FFFE83',
  green: '#C6EFCE',
  white: '#FFFFFF'
}
