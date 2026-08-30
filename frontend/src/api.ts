import axios from 'axios'

export const http = axios.create({ baseURL: '/api', timeout: 60000 })

export interface Station { id: number; code: string; name: string; aliases: string[] }
export interface Staff {
  id: string; station_code: string; name: string; role: string; note: string; is_active: boolean
}
export interface BatchSummary {
  id: string; start_date: string; end_date: string; handover_date: string; status: string
  stations: string[]; item_total: number; pending_review: number; created_at: string
}
export interface HandoverItemView {
  id: string; work_item_id: string; title: string; status: string; priority: string
  section: 'important' | 'handover'; completed_by: string; sort_order: number
  summary: string; latest_progress: string; blocker: string; next_action: string
  previous_owner: string; next_owner: string; start_date: string | null; end_date: string | null
  review_status: string; human_edited: boolean; revision: number; source_ids: string[]
  color: 'red' | 'yellow' | 'white'
}
export interface GeneralItemView {
  id: string; plan_id: string; library_id: string; title: string; category: string
  plan_start: string | null; plan_end: string | null; status: string; owner: string
  note: string; revision: number; overdue: boolean; color: string
  template_meta: Record<string, string> | null
}
export interface DeviceChangeView { id: string; content: string; revision: number }
export interface ExternalAssessmentView {
  id: string; contractor: string; work_content: string; assessment: string; remark: string
  sort_order: number; revision: number; source_type: string
}
export interface StationDetail {
  station_meta_id: string; station_id: number; station_code: string; station_name: string
  duty_leader: string; temp_leader: string; operators: string[]; items: HandoverItemView[]
  general: { monthly: GeneralItemView[]; quarterly: GeneralItemView[]; yearly: GeneralItemView[] }
  device_changes: DeviceChangeView[]; external_assessments: ExternalAssessmentView[]
  snapshots: { id: string; version: number; status: string; created_at: string; docx_path: string }[]
}
export interface BatchDetail {
  id: string; start_date: string; end_date: string; handover_date: string
  status: string; created_at: string; stations: StationDetail[]
}
export interface SourceRow { date: string; text: string; sheet: string; row_no: number | null; status_hint: string }
export interface ImportResult {
  status: 'success' | 'failed'; job_id?: string; inserted?: number; skipped_duplicate?: number
  date_unresolved?: Array<{ sheet: string; row: number; date: string }>; error?: string
}
export interface RenderResult {
  snapshot_id: string; version: number; sha256: string; docx_path: string
  current_path: string; cloud_path: string | null; download_url: string
  validation?: { valid: boolean; errors: string[]; warnings: string[] }
}
export interface ImportPreviewRow {
  preview_key: string; kind: 'item' | 'external'; section: 'important' | 'handover' | 'external'
  include: boolean; valid: boolean; duplicate: boolean; errors: string[]; warnings: string[]
  title_snapshot?: string; status?: string; priority?: string; completed_by?: string
  previous_owner?: string; next_owner?: string; start_date?: string | null; end_date?: string | null
  summary?: string; latest_progress?: string; blocker?: string; next_action?: string
  contractor?: string; work_content?: string; assessment?: string; remark?: string
  source: { sheet: string; row_no: number; raw: Record<string, unknown> }
}
export interface ImportPreview {
  id: string; batch_id: string; station_meta_id: string; parser_key: string
  source_file_name: string; source_sha256: string; status: string; rows: ImportPreviewRow[]
  warnings: Array<{ sheet: string; field: string; reason: string }>
  summary: { total: number; important: number; handover: number; external: number; invalid: number; duplicate: number }
  result: Record<string, unknown>
}

export const api = {
  stations: () => http.get<Station[]>('/stations').then(r => r.data),
  listBatches: () => http.get<BatchSummary[]>('/handovers').then(r => r.data),
  createBatch: (body: Record<string, unknown>) =>
    http.post<{ id: string; status: string }>('/handovers', body).then(r => r.data),
  batchDetail: (id: string) => http.get<BatchDetail>(`/handovers/${id}`).then(r => r.data),
  patchMeta: (id: string, fields: Record<string, unknown>) =>
    http.patch(`/handover-station-meta/${id}`, fields).then(r => r.data),
  staff: (stationCode?: string) => http.get<Staff[]>('/staff', {
    params: stationCode ? { station_code: stationCode } : {}
  }).then(r => r.data),

  addItem: (batchId: string, fields: Record<string, unknown>) =>
    http.post(`/handovers/${batchId}/items`, fields).then(r => r.data),
  patchItem: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.patch(`/handover-items/${id}`, { revision, ...fields }).then(r => r.data),
  reviewItem: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.post(`/handover-items/${id}/review`, { revision, ...fields }).then(r => r.data),
  approveItem: (id: string, revision: number) =>
    http.post(`/handover-items/${id}/approve`, { revision }).then(r => r.data),
  approveAll: (batchId: string, stationMetaId: string, section?: string) =>
    http.post<{ approved: number }>(`/handovers/${batchId}/approve-all`, {
      station_meta_id: stationMetaId, section
    }).then(r => r.data),
  deleteItem: (id: string, revision: number) =>
    http.delete(`/handover-items/${id}`, { data: { revision } }).then(r => r.data),
  reorderItems: (batchId: string, stationMetaId: string, section: string, orderedIds: string[]) =>
    http.post(`/handovers/${batchId}/items/reorder`, {
      station_meta_id: stationMetaId, section, ordered_ids: orderedIds
    }).then(r => r.data),
  itemSources: (id: string) => http.get<SourceRow[]>(`/work-items/${id}/sources`).then(r => r.data),

  addDeviceChange: (batchId: string, stationMetaId: string, content: string) =>
    http.post(`/handovers/${batchId}/device-changes`, { station_meta_id: stationMetaId, content }).then(r => r.data),
  patchDeviceChange: (id: string, revision: number, content: string) =>
    http.patch(`/device-changes/${id}`, { revision, content }).then(r => r.data),
  deleteDeviceChange: (id: string, revision: number) =>
    http.delete(`/device-changes/${id}`, { data: { revision } }).then(r => r.data),

  addExternal: (batchId: string, fields: Record<string, unknown>) =>
    http.post(`/handovers/${batchId}/external-assessments`, fields).then(r => r.data),
  patchExternal: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.patch(`/external-assessments/${id}`, { revision, ...fields }).then(r => r.data),
  deleteExternal: (id: string, revision: number) =>
    http.delete(`/external-assessments/${id}`, { data: { revision } }).then(r => r.data),
  reorderExternal: (batchId: string, stationMetaId: string, orderedIds: string[]) =>
    http.post(`/handovers/${batchId}/external-assessments/reorder`, {
      station_meta_id: stationMetaId, ordered_ids: orderedIds
    }).then(r => r.data),

  patchGeneralItem: (id: string, revision: number, fields: Record<string, unknown>) =>
    http.patch(`/general-items/${id}`, { revision, ...fields }).then(r => r.data),
  render: (batchId: string, stationMetaId: string) =>
    http.post<RenderResult>(`/handovers/${batchId}/render`, { station_meta_id: stationMetaId }).then(r => r.data),
  downloadUrl: (snapshotId: string) => `/api/documents/${snapshotId}/download`,

  handoverTemplateUrl: () => '/api/imports/handover-template',
  previewImport: (batchId: string, stationMetaId: string, file: File) => {
    const body = new FormData()
    body.append('station_meta_id', stationMetaId)
    body.append('file', file)
    return http.post<ImportPreview>(`/handovers/${batchId}/imports/preview`, body, { timeout: 120000 }).then(r => r.data)
  },
  commitImport: (batchId: string, previewId: string, rows: ImportPreviewRow[]) =>
    http.post(`/handovers/${batchId}/imports/${previewId}/commit`, { rows }, { timeout: 120000 }).then(r => r.data),

  importMeeting: (file: File, options: { defaultYear: number; stationCode?: string }) => {
    const body = new FormData()
    body.append('file', file)
    body.append('default_year', String(options.defaultYear))
    if (options.stationCode) body.append('station_code', options.stationCode)
    return http.post<ImportResult>('/imports/xlsx', body, { timeout: 120000 }).then(r => r.data)
  },
  importPlan: (file: File, options: { planMonth: string; category: string; defaultYear: number; stationCode?: string }) => {
    const body = new FormData()
    body.append('file', file)
    body.append('plan_month', options.planMonth)
    body.append('category', options.category)
    body.append('default_year', String(options.defaultYear))
    if (options.stationCode) body.append('station_code', options.stationCode)
    return http.post<ImportResult>('/imports/monthly-plan', body, { timeout: 120000 }).then(r => r.data)
  }
}

export function cnDate(iso?: string | null): string {
  if (!iso) return '—'
  const [year, month, day] = iso.split('-')
  return `${Number(year)}.${Number(month)}.${Number(day)}`
}
export function cnDateTime(iso?: string | null): string {
  if (!iso) return '—'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return iso
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(value)
}

export const ITEM_STATUS_LABEL: Record<string, string> = {
  pending: '待启动', in_progress: '进行中', blocked: '受阻', completed: '已完成', unknown: '待确认'
}
export const REVIEW_LABEL: Record<string, string> = {
  pending: '待复核', approved: '已确认', edited: '已编辑', rejected: '已退回'
}
export const PRIORITY_LABEL: Record<string, string> = { urgent: '紧急', important: '重点', normal: '普通' }
export const COLOR_HEX: Record<string, string> = {
  red: '#FFEBEE', yellow: '#FFF8D8', green: '#E7F7ED', white: '#FFFFFF'
}
