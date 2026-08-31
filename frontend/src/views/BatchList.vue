<template>
  <div class="dashboard">
    <section class="welcome-card">
      <div class="welcome-copy">
        <span class="eyebrow">交接班工作台</span>
        <h1>今天的交接，从这里开始</h1>
        <p>先导入班会记录，再创建交接班；系统会自动整理事项、保留来源并生成 Word。</p>
      </div>
      <div class="welcome-actions">
        <el-button size="large" @click="openImport">导入数据</el-button>
        <el-button type="primary" size="large" @click="openCreate">＋ 新建交接班</el-button>
      </div>
    </section>

    <section class="stat-grid" aria-label="交接班概况">
      <div class="stat-card">
        <span class="stat-icon blue">班</span>
        <div><strong>{{ batches.length }}</strong><span>累计班次</span></div>
      </div>
      <div class="stat-card">
        <span class="stat-icon amber">核</span>
        <div><strong>{{ pendingTotal }}</strong><span>待复核事项</span></div>
      </div>
      <div class="stat-card">
        <span class="stat-icon green">备</span>
        <div><strong>{{ readyTotal }}</strong><span>可生成班次</span></div>
      </div>
      <div class="stat-card">
        <span class="stat-icon purple">文</span>
        <div><strong>{{ publishedTotal }}</strong><span>已发布班次</span></div>
      </div>
    </section>

    <section class="workflow-card">
      <div class="section-heading compact">
        <div>
          <span class="section-kicker">推荐流程</span>
          <h2>四步完成一次交接</h2>
        </div>
        <span class="section-note">按顺序操作，不容易遗漏</span>
      </div>
      <div class="workflow-steps">
        <button class="flow-step" type="button" @click="openImport">
          <span class="step-number">1</span>
          <span class="step-copy"><strong>导入记录</strong><small>XLSX 拖进来即可</small></span>
          <span class="step-arrow">›</span>
        </button>
        <button class="flow-step" type="button" @click="openCreate">
          <span class="step-number">2</span>
          <span class="step-copy"><strong>创建班次</strong><small>日期与场站已预填</small></span>
          <span class="step-arrow">›</span>
        </button>
        <button class="flow-step" type="button" @click="openLatestPending">
          <span class="step-number">3</span>
          <span class="step-copy"><strong>快速复核</strong><small>{{ pendingTotal ? `${pendingTotal} 条待处理` : '当前没有待办' }}</small></span>
          <span class="step-arrow">›</span>
        </button>
        <button class="flow-step" type="button" @click="openLatestPublished">
          <span class="step-number">4</span>
          <span class="step-copy"><strong>生成 Word</strong><small>自动留存历史版本</small></span>
          <span class="step-arrow">›</span>
        </button>
      </div>
    </section>

    <section class="history-card" v-loading="loading">
      <div class="section-heading history-heading">
        <div>
          <span class="section-kicker">最近工作</span>
          <h2>交接班记录</h2>
        </div>
        <div class="history-filters">
          <el-input v-model="keyword" clearable placeholder="搜索日期或场站" class="search-input" />
          <el-select v-model="statusFilter" class="status-select" aria-label="筛选状态">
            <el-option label="全部状态" value="all" />
            <el-option label="待复核" value="review" />
            <el-option label="可生成" value="ready" />
            <el-option label="已发布" value="published" />
          </el-select>
        </div>
      </div>

      <el-table v-if="filteredBatches.length" :data="filteredBatches" class="batch-table" row-key="id">
        <el-table-column label="交接时间" min-width="210">
          <template #default="{ row }">
            <div class="date-main">{{ cnDate(row.start_date) }} — {{ cnDate(row.end_date) }}</div>
            <div class="cell-sub">交接日 {{ cnDate(row.handover_date) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="场站" min-width="230">
          <template #default="{ row }">
            <div class="station-tags">
              <el-tag v-for="s in row.stations" :key="s" size="small" effect="plain">{{ s }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="复核进度" min-width="210">
          <template #default="{ row }">
            <div class="progress-line">
              <span>{{ reviewedCount(row) }} / {{ row.item_total }} 条</span>
              <span>{{ reviewPercent(row) }}%</span>
            </div>
            <el-progress :percentage="reviewPercent(row)" :show-text="false" :stroke-width="7"
                         :status="row.pending_review === 0 ? 'success' : undefined" />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="batchTagType(row)" effect="light" round>{{ batchStatus(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="155">
          <template #default="{ row }"><span class="cell-sub">{{ cnDateTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link @click="router.push(`/batch/${row.id}`)">
              {{ row.pending_review ? '继续复核' : row.status === 'published' ? '查看版本' : '检查生成' }} →
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else :description="batches.length ? '没有符合条件的记录' : '还没有交接班记录'">
        <el-button type="primary" @click="openCreate">创建第一个交接班</el-button>
      </el-empty>
    </section>

    <el-dialog v-model="showCreate" title="新建交接班" width="min(640px, calc(100vw - 28px))"
               align-center destroy-on-close>
      <div class="dialog-intro">
        <span class="dialog-icon">快</span>
        <div><strong>常用内容已预填</strong><p>默认最近 10 天、交接日取截止日、场站沿用上次选择。</p></div>
      </div>
      <el-form label-position="top" class="create-form">
        <el-form-item label="交接时间范围" required>
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
                          range-separator="至" start-placeholder="开始日期" end-placeholder="截止日期"
                          unlink-panels class="full-control" @change="syncHandoverDate" />
          <div class="quick-ranges">
            <span>快捷选择：</span>
            <el-button link type="primary" @click="setQuickRange(7)">最近 7 天</el-button>
            <el-button link type="primary" @click="setQuickRange(10)">最近 10 天</el-button>
          </div>
        </el-form-item>
        <el-form-item label="交接班日期" required>
          <el-date-picker v-model="form.handover_date" type="date" value-format="YYYY-MM-DD"
                          placeholder="选择交接日" class="full-control" />
          <span class="field-tip">通常与截止日期相同，时间范围修改后会自动同步。</span>
        </el-form-item>
        <el-form-item required>
          <template #label>
            <div class="label-with-action">
              <span>场站</span>
              <el-button link type="primary" @click="toggleAllStations">
                {{ form.station_ids.length === stations.length ? '清空' : '全选' }}
              </el-button>
            </div>
          </template>
          <el-select v-model="form.station_ids" multiple collapse-tags collapse-tags-tooltip
                     placeholder="请选择场站" class="full-control">
            <el-option v-for="s in stations" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-if="createSummary" class="create-summary">
        将整理 <strong>{{ createSummary.days }}</strong> 天内、<strong>{{ createSummary.stations }}</strong> 个场站的记录
      </div>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建并进入复核</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showImport" title="导入数据" width="min(680px, calc(100vw - 28px))"
               align-center destroy-on-close @closed="resetImport">
      <el-alert title="重复导入同一份数据不会产生重复记录，可以放心操作。" type="info" :closable="false" show-icon />
      <el-tabs v-model="importTab" class="import-tabs">
        <el-tab-pane label="班会记录" name="meeting">
          <div class="tab-description">导入腾讯文档或 Excel 导出的班会记录，系统会自动识别日期和场站。</div>
          <el-upload drag :auto-upload="false" accept=".xlsx" :limit="1" :file-list="meetingFiles"
                     :on-change="onMeetingFile" :on-remove="removeMeetingFile" :on-exceed="onFileExceed">
            <div class="upload-symbol">⇧</div>
            <div class="el-upload__text">把 XLSX 拖到这里，或 <em>点击选择</em></div>
            <template #tip><div class="el-upload__tip">仅支持 .xlsx，最大 50 MB</div></template>
          </el-upload>
          <div class="import-options">
            <el-form-item label="记录年份">
              <el-input-number v-model="importForm.defaultYear" :min="2000" :max="2100" controls-position="right" />
            </el-form-item>
            <el-form-item label="场站识别">
              <el-select v-model="importForm.stationCode" placeholder="自动识别" clearable>
                <el-option label="自动识别（推荐）" value="" />
                <el-option v-for="s in stations" :key="s.code" :label="s.name" :value="s.code" />
              </el-select>
            </el-form-item>
          </div>
        </el-tab-pane>
        <el-tab-pane label="定期工作计划" name="plan">
          <div class="tab-description">导入月度、季度或年度计划；内置模板之外的实际计划可在这里补充。</div>
          <el-upload drag :auto-upload="false" accept=".xlsx" :limit="1" :file-list="planFiles"
                     :on-change="onPlanFile" :on-remove="removePlanFile" :on-exceed="onFileExceed">
            <div class="upload-symbol">⇧</div>
            <div class="el-upload__text">把计划 XLSX 拖到这里，或 <em>点击选择</em></div>
            <template #tip><div class="el-upload__tip">仅支持 .xlsx，重复内容会自动跳过</div></template>
          </el-upload>
          <div class="import-options plan-options">
            <el-form-item label="计划月份">
              <el-date-picker v-model="importForm.planMonth" type="month" value-format="YYYY-MM" placeholder="选择月份" />
            </el-form-item>
            <el-form-item label="计划类型">
              <el-select v-model="importForm.category">
                <el-option label="月度" value="monthly" />
                <el-option label="季度" value="quarterly" />
                <el-option label="年度" value="yearly" />
              </el-select>
            </el-form-item>
            <el-form-item label="适用场站">
              <el-select v-model="importForm.stationCode" clearable placeholder="片区通用">
                <el-option label="片区通用" value="" />
                <el-option v-for="s in stations" :key="s.code" :label="s.name" :value="s.code" />
              </el-select>
            </el-form-item>
          </div>
        </el-tab-pane>
      </el-tabs>

      <div v-if="importResult" class="import-result" :class="importResult.status">
        <strong>{{ importResult.status === 'success' ? '导入完成' : '导入失败' }}</strong>
        <span v-if="importResult.status === 'success'">
          新增 {{ importResult.inserted || 0 }} 条，跳过重复 {{ importResult.skipped_duplicate || 0 }} 条。
        </span>
        <span v-else>{{ importResult.error || '文件无法读取，请检查格式。' }}</span>
        <span v-if="importResult.date_unresolved?.length" class="result-warning">
          另有 {{ importResult.date_unresolved.length }} 行日期未识别，请检查原表年份或日期格式。
        </span>
      </div>

      <template #footer>
        <el-button @click="showImport = false">完成</el-button>
        <el-button type="primary" :loading="importing" @click="runImport">
          {{ importing ? '正在导入…' : '开始导入' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, cnDate, cnDateTime, type BatchSummary, type ImportResult, type Station } from '@/api'

const router = useRouter()
const loading = ref(false)
const batches = ref<BatchSummary[]>([])
const stations = ref<Station[]>([])
const keyword = ref('')
const statusFilter = ref('all')

const showCreate = ref(false)
const creating = ref(false)
const dateRange = ref<string[]>([])
const form = reactive({ handover_date: '', station_ids: [] as number[] })

const showImport = ref(false)
const importTab = ref<'meeting' | 'plan'>('meeting')
const importing = ref(false)
const importResult = ref<ImportResult | null>(null)
const meetingFile = ref<File | null>(null)
const planFile = ref<File | null>(null)
const meetingFiles = ref<any[]>([])
const planFiles = ref<any[]>([])
const now = new Date()
const importForm = reactive({
  defaultYear: now.getFullYear(),
  stationCode: '',
  planMonth: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`,
  category: 'monthly'
})

const pendingTotal = computed(() => batches.value.reduce((sum, row) => sum + row.pending_review, 0))
const publishedTotal = computed(() => batches.value.filter(row => row.status === 'published').length)
const readyTotal = computed(() => batches.value.filter(row => !row.pending_review && row.status !== 'published').length)

const filteredBatches = computed(() => {
  const key = keyword.value.trim().toLowerCase()
  return batches.value.filter(row => {
    const matchesKeyword = !key || [row.start_date, row.end_date, row.handover_date, ...row.stations]
      .join(' ').toLowerCase().includes(key)
    const matchesStatus = statusFilter.value === 'all'
      || (statusFilter.value === 'review' && row.pending_review > 0)
      || (statusFilter.value === 'ready' && row.pending_review === 0 && row.status !== 'published')
      || (statusFilter.value === 'published' && row.status === 'published')
    return matchesKeyword && matchesStatus
  })
})

const createSummary = computed(() => {
  if (dateRange.value.length !== 2 || !form.station_ids.length) return null
  const start = new Date(`${dateRange.value[0]}T00:00:00`)
  const end = new Date(`${dateRange.value[1]}T00:00:00`)
  const days = Math.floor((end.getTime() - start.getTime()) / 86400000) + 1
  return { days: Math.max(days, 0), stations: form.station_ids.length }
})

function localIso(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
}

function setQuickRange(days: number) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days + 1)
  dateRange.value = [localIso(start), localIso(end)]
  form.handover_date = localIso(end)
}

function syncHandoverDate() {
  if (dateRange.value.length === 2) form.handover_date = dateRange.value[1]
}

function rememberedStations() {
  try {
    const values = JSON.parse(localStorage.getItem('jx-handover:last-stations') || '[]') as number[]
    const valid = values.filter(id => stations.value.some(station => station.id === id))
    return valid.length ? valid : stations.value.map(station => station.id)
  } catch {
    return stations.value.map(station => station.id)
  }
}

function openCreate() {
  setQuickRange(10)
  form.station_ids = rememberedStations()
  showCreate.value = true
}

function toggleAllStations() {
  form.station_ids = form.station_ids.length === stations.value.length
    ? [] : stations.value.map(station => station.id)
}

async function load() {
  loading.value = true
  try {
    batches.value = await api.listBatches()
  } catch {
    ElMessage.error('交接班记录加载失败，请确认系统服务已启动')
  } finally {
    loading.value = false
  }
}

async function create() {
  if (dateRange.value.length !== 2 || !form.handover_date) {
    ElMessage.warning('请补充完整的交接日期')
    return
  }
  if (!form.station_ids.length) {
    ElMessage.warning('请至少选择一个场站')
    return
  }
  const [startDate, endDate] = dateRange.value
  if (startDate > endDate || form.handover_date < startDate || form.handover_date > endDate) {
    ElMessage.warning('交接班日期需要位于所选时间范围内')
    return
  }
  creating.value = true
  try {
    const result = await api.createBatch({
      start_date: startDate,
      end_date: endDate,
      handover_date: form.handover_date,
      station_ids: [...form.station_ids]
    })
    localStorage.setItem('jx-handover:last-stations', JSON.stringify(form.station_ids))
    ElMessage.success('班次已创建，事项整理完成')
    showCreate.value = false
    await router.push(`/batch/${result.id}`)
  } catch (error: any) {
    ElMessage.error(friendlyError(error, '创建失败，请稍后重试'))
  } finally {
    creating.value = false
  }
}

function reviewedCount(row: BatchSummary) {
  return Math.max(row.item_total - row.pending_review, 0)
}

function reviewPercent(row: BatchSummary) {
  return row.item_total ? Math.round(reviewedCount(row) / row.item_total * 100) : 100
}

function batchStatus(row: BatchSummary) {
  if (row.status === 'published') return '已发布'
  if (row.pending_review === 0) return '可生成'
  return '待复核'
}

function batchTagType(row: BatchSummary) {
  if (row.status === 'published') return 'success'
  if (row.pending_review === 0) return 'primary'
  return 'warning'
}

function openLatestPending() {
  const target = batches.value.find(row => row.pending_review > 0)
  if (target) router.push(`/batch/${target.id}`)
  else ElMessage.success('很好，当前没有待复核事项')
}

function openLatestPublished() {
  const target = batches.value.find(row => row.pending_review === 0 && row.status !== 'published')
    || batches.value.find(row => row.status === 'published')
    || batches.value[0]
  if (target) router.push(`/batch/${target.id}`)
  else openCreate()
}

function openImport() {
  importResult.value = null
  showImport.value = true
}

function validateUpload(file: File) {
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    ElMessage.warning('请选择 .xlsx 文件')
    return false
  }
  if (file.size > 50 * 1024 * 1024) {
    ElMessage.warning('文件不能超过 50 MB')
    return false
  }
  return true
}

function onMeetingFile(upload: any) {
  if (!upload.raw || !validateUpload(upload.raw)) {
    meetingFiles.value = []
    meetingFile.value = null
    return
  }
  meetingFile.value = upload.raw
  meetingFiles.value = [upload]
  importResult.value = null
}

function onPlanFile(upload: any) {
  if (!upload.raw || !validateUpload(upload.raw)) {
    planFiles.value = []
    planFile.value = null
    return
  }
  planFile.value = upload.raw
  planFiles.value = [upload]
  importResult.value = null
}

function removeMeetingFile() {
  meetingFile.value = null
  meetingFiles.value = []
}

function removePlanFile() {
  planFile.value = null
  planFiles.value = []
}

function onFileExceed() {
  ElMessage.info('一次导入一个文件；请先移除当前文件再选择')
}

async function runImport() {
  const selected = importTab.value === 'meeting' ? meetingFile.value : planFile.value
  if (!selected) {
    ElMessage.warning('请先选择要导入的 XLSX 文件')
    return
  }
  if (importTab.value === 'plan' && !importForm.planMonth) {
    ElMessage.warning('请选择计划月份')
    return
  }
  importing.value = true
  importResult.value = null
  try {
    const result = importTab.value === 'meeting'
      ? await api.importMeeting(selected, {
        defaultYear: importForm.defaultYear,
        stationCode: importForm.stationCode || undefined
      })
      : await api.importPlan(selected, {
        planMonth: importForm.planMonth,
        category: importForm.category,
        defaultYear: importForm.defaultYear,
        stationCode: importForm.stationCode || undefined
      })
    importResult.value = result
    if (result.status === 'success') ElMessage.success(`导入完成：新增 ${result.inserted || 0} 条`)
    else ElMessage.error(result.error || '导入失败')
  } catch (error: any) {
    const message = friendlyError(error, '导入失败，请检查文件格式')
    importResult.value = { status: 'failed', error: message }
    ElMessage.error(message)
  } finally {
    importing.value = false
  }
}

function resetImport() {
  meetingFile.value = null
  planFile.value = null
  meetingFiles.value = []
  planFiles.value = []
  importResult.value = null
}

function friendlyError(error: any, fallback: string) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return error?.message || fallback
}

async function loadPage() {
  try {
    stations.value = await api.stations()
  } catch {
    ElMessage.error('场站信息加载失败')
  }
  await load()
}

function refreshAfterReconnect() { loadPage() }

onMounted(() => {
  window.addEventListener('jx-data-refresh', refreshAfterReconnect)
  loadPage()
})
onBeforeUnmount(() => window.removeEventListener('jx-data-refresh', refreshAfterReconnect))
</script>

<style scoped>
.dashboard {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  min-width: 0;
}

.dashboard > section {
  min-width: 0;
}

.welcome-card {
  min-height: 178px;
  padding: 30px 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  overflow: hidden;
  position: relative;
  border-radius: 18px;
  color: #fff;
  background: linear-gradient(118deg, #173d69 0%, #2464a5 68%, #3380c7 100%);
  box-shadow: 0 14px 36px rgba(25, 71, 119, 0.2);
}

.welcome-card::after {
  content: "";
  position: absolute;
  width: 260px;
  height: 260px;
  right: 18%;
  top: -155px;
  border: 46px solid rgba(255, 255, 255, 0.06);
  border-radius: 50%;
}

.welcome-copy,
.welcome-actions {
  z-index: 1;
  min-width: 0;
}

.eyebrow,
.section-kicker {
  color: #7da8d5;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.welcome-card .eyebrow {
  color: #bdd8f3;
}

.welcome-card h1 {
  margin: 8px 0 10px;
  font-size: clamp(25px, 3vw, 34px);
  line-height: 1.25;
  letter-spacing: 0.01em;
}

.welcome-card p {
  max-width: 680px;
  margin: 0;
  color: #d8e6f4;
  font-size: 14px;
  line-height: 1.7;
}

.welcome-actions {
  display: flex;
  flex-shrink: 0;
  gap: 10px;
}

.welcome-actions :deep(.el-button) {
  min-width: 126px;
  border-radius: 10px;
}

.welcome-actions :deep(.el-button--default) {
  color: #1d5389;
  border-color: #fff;
  background: #fff;
}

.welcome-actions :deep(.el-button--primary) {
  border-color: #72bbff;
  background: #1377d4;
  box-shadow: 0 8px 20px rgba(7, 51, 94, 0.22);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.stat-card {
  min-width: 0;
  min-height: 94px;
  padding: 18px 20px;
  display: flex;
  align-items: center;
  gap: 15px;
  border: 1px solid #e6edf5;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 5px 16px rgba(30, 62, 98, 0.05);
}

.stat-icon {
  width: 45px;
  height: 45px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 13px;
  font-size: 15px;
  font-weight: 800;
}

.stat-icon.blue { color: #2167ae; background: #e9f3ff; }
.stat-icon.amber { color: #b26a0a; background: #fff2da; }
.stat-icon.green { color: #168059; background: #e4f8ef; }
.stat-icon.purple { color: #7651ac; background: #f2ebff; }

.stat-card div {
  display: flex;
  flex-direction: column;
}

.stat-card strong {
  color: #152b45;
  font-size: 25px;
  line-height: 1.1;
}

.stat-card span:last-child {
  margin-top: 5px;
  color: #728197;
  font-size: 12px;
}

.workflow-card,
.history-card {
  min-width: 0;
  padding: 22px 24px;
  border: 1px solid #e3ebf4;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 8px 24px rgba(27, 59, 94, 0.055);
}

.section-heading {
  margin-bottom: 18px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
}

.section-heading.compact {
  margin-bottom: 16px;
}

.section-heading h2 {
  margin: 4px 0 0;
  color: #1c3048;
  font-size: 19px;
}

.section-note,
.cell-sub {
  color: #8491a3;
  font-size: 12px;
}

.workflow-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.flow-step {
  min-width: 0;
  padding: 14px;
  display: flex;
  align-items: center;
  gap: 11px;
  color: inherit;
  border: 1px solid #e7edf4;
  border-radius: 12px;
  background: #f9fbfd;
  cursor: pointer;
  text-align: left;
  transition: 0.18s ease;
}

.flow-step:hover {
  border-color: #9fc7ef;
  background: #f3f8fe;
  transform: translateY(-1px);
}

.step-number {
  width: 31px;
  height: 31px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 9px;
  color: #fff;
  background: #2769ad;
  font-size: 13px;
  font-weight: 800;
}

.step-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.step-copy strong {
  color: #263d57;
  font-size: 14px;
}

.step-copy small {
  overflow: hidden;
  color: #8995a4;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-arrow {
  color: #a6b2c1;
  font-size: 22px;
}

.history-heading {
  align-items: center;
}

.history-filters {
  display: flex;
  gap: 10px;
}

.search-input { width: 220px; }
.status-select { width: 125px; }

.batch-table :deep(.el-table__header th) {
  height: 44px;
  color: #65758a;
  background: #f7f9fc;
  font-size: 12px;
  font-weight: 700;
}

.batch-table :deep(.el-table__row td) {
  padding: 14px 0;
}

.date-main {
  color: #263b53;
  font-size: 13px;
  font-weight: 650;
}

.station-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.progress-line {
  margin-bottom: 6px;
  display: flex;
  justify-content: space-between;
  color: #65758a;
  font-size: 11px;
}

.dialog-intro {
  margin-bottom: 18px;
  padding: 13px 15px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #dbeafa;
  border-radius: 12px;
  background: #f3f8fe;
}

.dialog-icon {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  color: #2567aa;
  border-radius: 10px;
  background: #deedfd;
  font-weight: 800;
}

.dialog-intro strong { color: #28435f; font-size: 14px; }
.dialog-intro p { margin: 3px 0 0; color: #718096; font-size: 12px; }

.create-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.full-control { width: 100% !important; }

.quick-ranges {
  width: 100%;
  margin-top: 5px;
  color: #8a96a5;
  font-size: 11px;
}

.quick-ranges :deep(.el-button) {
  height: auto;
  padding: 0 4px;
  font-size: 11px;
}

.field-tip {
  margin-top: 5px;
  color: #9aa5b2;
  font-size: 11px;
  line-height: 1.5;
}

.label-with-action {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.label-with-action :deep(.el-button) {
  height: auto;
  padding: 0;
}

.create-summary {
  padding: 11px 14px;
  color: #52677f;
  border-radius: 9px;
  background: #f6f8fb;
  font-size: 12px;
}

.import-tabs {
  margin-top: 15px;
}

.tab-description {
  margin: 4px 0 14px;
  color: #66768a;
  font-size: 12px;
  line-height: 1.6;
}

.upload-symbol {
  color: #5d89b8;
  font-size: 34px;
  line-height: 1;
}

.import-options {
  margin-top: 15px;
  padding: 14px 14px 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 14px;
  border-radius: 10px;
  background: #f7f9fc;
}

.plan-options {
  grid-template-columns: repeat(3, 1fr);
}

.import-options :deep(.el-form-item__label) {
  color: #718096;
  font-size: 11px;
}

.import-options :deep(.el-select),
.import-options :deep(.el-date-editor) {
  width: 100%;
}

.import-result {
  margin-top: 14px;
  padding: 12px 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px 10px;
  border-radius: 9px;
  font-size: 12px;
}

.import-result.success { color: #227354; background: #edf9f4; }
.import-result.failed { color: #a83c3c; background: #fff1f1; }
.result-warning { width: 100%; color: #a46a0c; }

@media (max-width: 980px) {
  .stat-grid,
  .workflow-steps {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .welcome-card {
    align-items: flex-start;
    flex-direction: column;
  }
}

@media (max-width: 640px) {
  .dashboard { gap: 14px; }

  .welcome-card {
    min-height: 0;
    padding: 22px 19px;
    border-radius: 14px;
  }

  .welcome-actions {
    width: 100%;
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .welcome-actions :deep(.el-button) {
    min-width: 0;
    flex: 1;
    margin: 0;
  }

  .stat-grid { gap: 8px; }

  .stat-card {
    min-height: 78px;
    padding: 13px;
    gap: 10px;
  }

  .stat-icon {
    width: 36px;
    height: 36px;
    border-radius: 10px;
  }

  .stat-card strong { font-size: 20px; }

  .workflow-card,
  .history-card {
    padding: 17px 14px;
    border-radius: 13px;
  }

  .workflow-steps {
    grid-template-columns: 1fr;
  }

  .section-note { display: none; }

  .history-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .history-filters,
  .search-input,
  .status-select {
    width: 100%;
  }

  .history-card {
    overflow: hidden;
  }

  .import-options,
  .plan-options {
    grid-template-columns: 1fr;
  }
}
</style>
