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
  general: { monthly: GeneralItemView[]; quarterly: GeneralItemView[] }
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
  approveItem: (id: string, revision: number) =>
    http.post(`/handover-items/${id}/approve`, { revision }).then(r => r.data),
  patchMeta: (metaId: string, fields: Record<string, unknown>) =>
    http.patch(`/handover-station-meta/${metaId}`, fields).then(r => r.data),
  addDeviceChange: (batchId: string, stationMetaId: string, content: string) =>
    http.post(`/handovers/${batchId}/device-changes`, {
      station_meta_id: stationMetaId,
      content
    }).then(r => r.data),
  itemSources: (workItemId: string) =>
    http.get<SourceRow[]>(`/work-items/${workItemId}/sources`).then(r => r.data),
  render: (batchId: string, stationMetaId: string) =>
    http
      .post(`/handovers/${batchId}/render`, { station_meta_id: stationMetaId })
      .then(r => r.data),
  downloadUrl: (snapshotId: string) => `/api/documents/${snapshotId}/download`
}

// ---------- 展示辅助 ----------
export function cnDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-')
  return `${Number(y)}.${Number(m)}.${Number(d)}`
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
  red: '#F4CCCC',
  yellow: '#FFF2CC',
  green: '#D9EAD3',
  white: '#FFFFFF'
}
