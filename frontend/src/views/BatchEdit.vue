<template>
  <div class="review-page" v-loading="loading">
    <template v-if="batch">
      <div class="review-topbar">
        <div>
          <el-button link class="back-button" @click="router.push('/')">← 返回工作台</el-button>
          <h1>{{ cnDate(batch.start_date) }} — {{ cnDate(batch.end_date) }} 交接班</h1>
          <p>交接日 {{ cnDate(batch.handover_date) }} · 所有修改自动留在数据库，生成 Word 前需完成复核</p>
        </div>
        <div class="top-actions">
          <el-button :loading="refreshing" @click="refresh">刷新数据</el-button>
          <el-button type="primary" @click="scrollToSection('publish')">去生成 Word ↓</el-button>
        </div>
      </div>

      <el-tabs v-model="activeStation" class="station-tabs">
        <el-tab-pane v-for="st in batch.stations" :key="st.station_meta_id" :name="st.station_meta_id">
          <template #label>
            <span class="station-tab-label">
              {{ st.station_name }}
              <span v-if="pendingCount(st)" class="tab-pending">{{ pendingCount(st) }}</span>
              <span v-else class="tab-done">✓</span>
            </span>
          </template>

          <section class="overview-card">
            <div class="progress-block">
              <el-progress type="circle" :percentage="stationProgress(st)" :width="80" :stroke-width="8"
                           :status="pendingCount(st) === 0 ? 'success' : undefined" />
              <div>
                <strong>{{ pendingCount(st) ? `还剩 ${pendingCount(st)} 条待复核` : '专业事项已复核完成' }}</strong>
                <span>已处理 {{ reviewedCount(st) }} / {{ st.items.length }} 条</span>
              </div>
            </div>
            <div class="overview-stats">
              <div><strong class="danger-text">{{ attentionCount(st) }}</strong><span>紧急 / 重点</span></div>
              <div><strong class="warning-text">{{ overdueCount(st) }}</strong><span>定期工作超期</span></div>
              <div><strong>{{ st.snapshots.length ? `V${padVersion(st.snapshots[0].version)}` : '—' }}</strong><span>最新 Word</span></div>
            </div>
            <nav class="section-nav" aria-label="页面章节">
              <button type="button" @click="scrollToSection('staff', st.station_meta_id)">人员</button>
              <button type="button" @click="scrollToSection('device', st.station_meta_id)">设备变更</button>
              <button type="button" @click="scrollToSection('items', st.station_meta_id)">专业事项</button>
              <button type="button" @click="scrollToSection('periodic', st.station_meta_id)">定期工作</button>
              <button type="button" class="primary-nav" @click="scrollToSection('publish', st.station_meta_id)">生成 Word</button>
            </nav>
          </section>

          <el-card :id="sectionId('staff', st.station_meta_id)" class="section-card" shadow="never">
            <template #header>
              <div class="card-heading">
                <div><span class="section-index">01</span><div><strong>值班人员</strong><small>选择后自动保存，无需再点保存按钮</small></div></div>
                <span class="save-indicator" :class="metaSaveState">
                  <i></i>{{ metaSaveLabel }}
                </span>
              </div>
            </template>
            <el-form label-position="top" class="staff-form">
              <el-form-item label="值班负责人">
                <el-select v-model="metaForm.duty_leader" filterable allow-create default-first-option
                           placeholder="请选择或输入姓名" @change="scheduleMetaSave">
                  <el-option v-for="person in staffList" :key="person.id"
                             :label="`${person.name}（${person.role}）`" :value="person.name" />
                </el-select>
              </el-form-item>
              <el-form-item label="临时值班负责人">
                <el-select v-model="metaForm.temp_leader" filterable allow-create default-first-option
                           placeholder="无" @change="scheduleMetaSave">
                  <el-option label="无" value="无" />
                  <el-option v-for="person in staffList" :key="person.id"
                             :label="`${person.name}（${person.role}）`" :value="person.name" />
                </el-select>
              </el-form-item>
              <el-form-item label="当班值班员">
                <el-select v-model="operatorsList" multiple filterable allow-create default-first-option
                           collapse-tags collapse-tags-tooltip placeholder="可选择多人" @change="scheduleMetaSave">
                  <el-option v-for="person in staffList" :key="person.id"
                             :label="`${person.name}（${person.role}）`" :value="person.name" />
                </el-select>
              </el-form-item>
            </el-form>
          </el-card>

          <el-card :id="sectionId('device', st.station_meta_id)" class="section-card" shadow="never">
            <template #header>
              <div class="card-heading">
                <div><span class="section-index">02</span><div><strong>设备变更情况</strong><small>只填写本班实际发生的设备状态变化</small></div></div>
              </div>
            </template>
            <div v-if="st.device_changes.length" class="device-list">
              <div v-for="(change, index) in st.device_changes" :key="change.id" class="device-item">
                <span>{{ index + 1 }}</span><p>{{ change.content }}</p>
              </div>
            </div>
            <div v-else class="empty-line">本班暂无设备变更</div>
            <div class="add-device-row">
              <el-input v-model="newDeviceChange" clearable placeholder="输入设备变更，按回车即可添加"
                        @keyup.enter="addDevice(st)" />
              <el-button :loading="addingDevice" @click="addDevice(st)">添加</el-button>
            </div>
          </el-card>

          <el-card :id="sectionId('items', st.station_meta_id)" class="section-card" shadow="never">
            <template #header>
              <div class="card-heading items-heading">
                <div><span class="section-index">03</span><div><strong>专业工作事项</strong><small>正确的事项可直接确认；需要改动时点开卡片</small></div></div>
                <el-popconfirm v-if="pendingCount(st)" width="270"
                               title="确认当前场站全部待复核事项均无误？"
                               confirm-button-text="全部确认" cancel-button-text="再检查一下"
                               @confirm="approveAll(st)">
                  <template #reference>
                    <el-button type="primary" plain :loading="bulkApproving">一键确认全部 {{ pendingCount(st) }} 条</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </template>

            <div class="item-tools">
              <el-radio-group v-model="itemFilter" size="small">
                <el-radio-button value="pending">待复核 {{ pendingCount(st) }}</el-radio-button>
                <el-radio-button value="attention">紧急 / 重点</el-radio-button>
                <el-radio-button value="completed">已完成</el-radio-button>
                <el-radio-button value="all">全部 {{ st.items.length }}</el-radio-button>
              </el-radio-group>
              <el-input v-model="itemKeyword" clearable placeholder="搜索标题、进展或责任人" class="item-search" />
            </div>

            <div v-if="filteredItems(st).length" class="item-list">
              <article v-for="item in filteredItems(st)" :key="item.id" class="item-card"
                       :class="[`priority-${item.priority}`, { reviewed: item.review_status !== 'pending' }]"
                       @click="openEdit(st, item)">
                <div class="item-main">
                  <div class="item-title-row">
                    <h3>{{ item.title }}</h3>
                    <div class="item-tags">
                      <el-tag size="small" :type="reviewTagType(item.review_status)" effect="light">
                        {{ STATUS_LABEL[item.review_status] }}
                      </el-tag>
                      <el-tag v-if="item.priority === 'urgent'" size="small" type="danger">紧急</el-tag>
                      <el-tag v-else-if="item.priority === 'important'" size="small" type="warning">重点</el-tag>
                      <el-tag size="small" :type="item.status === 'completed' ? 'success' : 'info'">
                        {{ workStatusLabel(item.status) }}
                      </el-tag>
                    </div>
                  </div>
                  <p v-if="item.latest_progress" class="item-progress">{{ item.latest_progress }}</p>
                  <div class="item-meta">
                    <span>{{ cnDate(item.start_date) }} — {{ cnDate(item.end_date) }}</span>
                    <span>{{ item.previous_owner || '未填' }} → {{ item.next_owner || '未填' }}</span>
                    <span>{{ item.section === 'important' ? '重点工作' : '需交接工作' }}</span>
                  </div>
                </div>
                <div class="item-action">
                  <el-button v-if="item.review_status === 'pending'" type="success" plain
                             :loading="approvingId === item.id" @click.stop="quickApprove(st, item)">
                    确认无误
                  </el-button>
                  <el-button v-else text @click.stop="openEdit(st, item)">查看 / 修改</el-button>
                </div>
              </article>
            </div>
            <el-empty v-else :image-size="76" :description="itemKeyword ? '没有匹配的事项' : '当前分类没有事项'" />
          </el-card>

          <el-card :id="sectionId('periodic', st.station_meta_id)" class="section-card" shadow="never">
            <template #header>
              <div class="card-heading">
                <div><span class="section-index">06</span><div><strong>定期工作完成情况</strong><small>勾选完成即自动保存，超期状态由系统计算</small></div></div>
              </div>
            </template>
            <el-tabs v-model="activePeriodic" type="border-card" class="periodic-tabs">
              <el-tab-pane v-for="section in PERIODIC_SECTIONS" :key="section.key" :name="section.key">
                <template #label>{{ section.title }}（{{ periodicRows(st, section.key).length }}）</template>
                <el-table v-if="periodicRows(st, section.key).length" :data="periodicRows(st, section.key)"
                          border size="small" :row-style="rowStyle" class="periodic-table">
                  <el-table-column label="工作内容" min-width="260">
                    <template #default="{ row }">
                      <el-tooltip v-if="row.template_meta?.content" :content="row.template_meta.content"
                                  placement="top" :show-after="350">
                        <span class="periodic-title">{{ row.title }}</span>
                      </el-tooltip>
                      <span v-else class="periodic-title">{{ row.title }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="截止日期" width="110" align="center">
                    <template #default="{ row }">{{ cnDate(row.plan_end) }}</template>
                  </el-table-column>
                  <el-table-column label="完成" width="100" align="center">
                    <template #default="{ row }">
                      <el-checkbox :model-value="row.status === 'completed'"
                                   @change="toggleGeneral(row, Boolean($event))">
                        {{ row.status === 'completed' ? '已完成' : '未完成' }}
                      </el-checkbox>
                    </template>
                  </el-table-column>
                  <el-table-column label="完成人" width="155">
                    <template #default="{ row }">
                      <el-select :model-value="row.owner" size="small" filterable clearable allow-create
                                 default-first-option placeholder="选择姓名"
                                 @change="saveGeneral(row, { owner: String($event || '') })">
                        <el-option v-for="person in staffList" :key="person.id"
                                   :label="person.name" :value="person.name" />
                      </el-select>
                    </template>
                  </el-table-column>
                  <el-table-column label="备注" min-width="170">
                    <template #default="{ row }">
                      <el-input :model-value="row.note" size="small" clearable placeholder="可选"
                                @change="saveGeneral(row, { note: String($event || '') })" />
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="80" align="center">
                    <template #default="{ row }">
                      <el-tag v-if="generalSavingIds.has(row.id)" size="small" type="info">保存中</el-tag>
                      <el-tag v-else-if="row.overdue" type="danger" size="small">超期</el-tag>
                      <el-tag v-else-if="row.status === 'completed'" type="success" size="small">完成</el-tag>
                      <span v-else class="muted">正常</span>
                    </template>
                  </el-table-column>
                </el-table>
                <el-empty v-else :image-size="68" description="本班没有此类定期工作" />
              </el-tab-pane>
            </el-tabs>
          </el-card>

          <el-card :id="sectionId('publish', st.station_meta_id)" class="section-card publish-card" shadow="never">
            <template #header>
              <div class="card-heading">
                <div><span class="section-index">✓</span><div><strong>生成正式 Word</strong><small>每次生成都会保留独立版本，可随时回看和下载</small></div></div>
              </div>
            </template>
            <div class="publish-panel" :class="{ ready: pendingCount(st) === 0 }">
              <div class="publish-state">
                <span class="publish-icon">{{ pendingCount(st) === 0 ? '✓' : pendingCount(st) }}</span>
                <div>
                  <strong>{{ pendingCount(st) === 0 ? '已满足生成条件' : '暂时不能生成' }}</strong>
                  <p v-if="pendingCount(st)">仍有 {{ pendingCount(st) }} 条专业事项待复核，处理完成后按钮会自动开启。</p>
                  <p v-else>系统将按模板生成 Word、校验文档完整性并登记新版本。</p>
                </div>
              </div>
              <el-button type="primary" size="large" :disabled="pendingCount(st) > 0"
                         :loading="rendering" @click="render(st)">
                {{ rendering ? '正在生成并校验…' : '生成并下载 Word' }}
              </el-button>
            </div>

            <div v-if="st.snapshots.length" class="version-area">
              <div class="latest-version">
                <div><span>最新版本</span><strong>V{{ padVersion(st.snapshots[0].version) }}</strong></div>
                <div class="latest-meta">生成于 {{ cnDateTime(st.snapshots[0].created_at) }}</div>
                <el-link type="primary" :href="api.downloadUrl(st.snapshots[0].id)" target="_blank">重新下载</el-link>
              </div>
              <el-collapse class="version-history">
                <el-collapse-item :title="`查看全部 ${st.snapshots.length} 个历史版本`" name="versions">
                  <el-table :data="st.snapshots" border size="small">
                    <el-table-column label="版本" width="85" align="center">
                      <template #default="{ row }">V{{ padVersion(row.version) }}</template>
                    </el-table-column>
                    <el-table-column label="生成时间" width="170">
                      <template #default="{ row }">{{ cnDateTime(row.created_at) }}</template>
                    </el-table-column>
                    <el-table-column prop="docx_path" label="文件位置" min-width="280" show-overflow-tooltip />
                    <el-table-column label="操作" width="90" align="center">
                      <template #default="{ row }">
                        <el-link type="primary" :href="api.downloadUrl(row.id)" target="_blank">下载</el-link>
                      </template>
                    </el-table-column>
                  </el-table>
                </el-collapse-item>
              </el-collapse>
            </div>
            <el-empty v-else :image-size="72" description="还没有生成过 Word 版本" />
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-drawer v-model="drawer" size="min(620px, 100vw)" append-to-body
               :before-close="beforeDrawerClose" class="review-drawer">
      <template #header>
        <div class="drawer-title">
          <span>事项复核</span>
          <small v-if="editMeta">{{ editMeta.station_name }} · 待复核 {{ pendingCount(editMeta) }} 条</small>
        </div>
      </template>
      <template v-if="editing">
        <el-alert title="修改内容后可直接“保存并确认”，系统会自动打开下一条待复核事项。"
                  type="info" :closable="false" show-icon />
        <el-form label-position="top" class="edit-form">
          <el-form-item label="事项标题" required>
            <el-input v-model="editing.title" type="textarea" :rows="2" resize="none" />
          </el-form-item>
          <div class="form-grid two">
            <el-form-item label="状态">
              <el-select v-model="editing.status">
                <el-option label="进行中" value="in_progress" />
                <el-option label="已完成" value="completed" />
                <el-option label="受阻" value="blocked" />
                <el-option label="待启动" value="pending" />
                <el-option label="未知" value="unknown" />
              </el-select>
            </el-form-item>
            <el-form-item label="优先级">
              <el-select v-model="editing.priority">
                <el-option label="紧急（红）" value="urgent" />
                <el-option label="重点（黄）" value="important" />
                <el-option label="普通（白）" value="normal" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="最新进展">
            <el-input v-model="editing.latest_progress" type="textarea" :rows="3" />
          </el-form-item>
          <div class="form-grid two">
            <el-form-item label="受阻原因">
              <el-input v-model="editing.blocker" placeholder="没有可留空" />
            </el-form-item>
            <el-form-item label="下一步">
              <el-input v-model="editing.next_action" placeholder="下一步安排" />
            </el-form-item>
          </div>
          <div class="form-grid two">
            <el-form-item label="交接前责任人">
              <el-select v-model="editing.previous_owner" filterable clearable allow-create default-first-option>
                <el-option v-for="person in staffList" :key="person.id" :label="person.name" :value="person.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="交接后责任人">
              <el-select v-model="editing.next_owner" filterable clearable allow-create default-first-option>
                <el-option v-for="person in staffList" :key="person.id" :label="person.name" :value="person.name" />
              </el-select>
            </el-form-item>
          </div>
          <div class="form-grid two">
            <el-form-item label="开始日期">
              <el-date-picker v-model="editing.start_date" type="date" value-format="YYYY-MM-DD" />
            </el-form-item>
            <el-form-item label="结束日期">
              <el-date-picker v-model="editing.end_date" type="date" value-format="YYYY-MM-DD" clearable />
            </el-form-item>
          </div>
        </el-form>

        <el-collapse class="source-collapse">
          <el-collapse-item name="sources">
            <template #title>原始来源记录（{{ sources.length }}）</template>
            <div v-loading="sourcesLoading">
              <div v-for="(source, index) in sources" :key="index" class="source-row">
                <div><strong>{{ cnDate(source.date) }}</strong><small>{{ source.sheet }}{{ source.row_no ? ` · 第 ${source.row_no} 行` : '' }}</small></div>
                <p>{{ source.text }}</p>
              </div>
              <div v-if="!sourcesLoading && !sources.length" class="empty-line">没有可显示的来源记录</div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </template>
      <template #footer>
        <div class="drawer-footer">
          <span class="keyboard-tip">快捷键 Ctrl + Enter</span>
          <div>
            <el-button @click="closeDrawer">取消</el-button>
            <el-button :disabled="!editDirty" :loading="saving" @click="saveEdit">仅保存修改</el-button>
            <el-button type="primary" :loading="saving" @click="saveAndApprove">
              保存并确认{{ nextPendingCount ? '，下一条' : '' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  api, cnDate, cnDateTime, COLOR_HEX, STATUS_LABEL,
  type BatchDetail, type GeneralItemView, type HandoverItemView,
  type SourceRow, type Staff, type StationDetail
} from '@/api'

const route = useRoute()
const router = useRouter()
const batchId = route.params.id as string
const loading = ref(false)
const refreshing = ref(false)
const batch = ref<BatchDetail | null>(null)
const activeStation = ref('')

const metaForm = reactive({ duty_leader: '', temp_leader: '无' })
const operatorsList = ref<string[]>([])
const staffList = ref<Staff[]>([])
const metaSaveState = ref<'idle' | 'dirty' | 'saving' | 'saved' | 'error'>('idle')
let metaSaveTimer: ReturnType<typeof setTimeout> | null = null

const newDeviceChange = ref('')
const addingDevice = ref(false)
const itemFilter = ref<'pending' | 'attention' | 'completed' | 'all'>('pending')
const itemKeyword = ref('')
const approvingId = ref('')
const bulkApproving = ref(false)
const activePeriodic = ref<'monthly' | 'quarterly' | 'yearly'>('monthly')
const generalSavingIds = ref(new Set<string>())
const rendering = ref(false)

const drawer = ref(false)
const editing = ref<HandoverItemView | null>(null)
const editMeta = ref<StationDetail | null>(null)
const originalEdit = ref('')
const sources = ref<SourceRow[]>([])
const sourcesLoading = ref(false)
const saving = ref(false)
let bypassCloseGuard = false

const PERIODIC_SECTIONS = [
  { key: 'monthly' as const, title: '月度' },
  { key: 'quarterly' as const, title: '季度' },
  { key: 'yearly' as const, title: '年度' }
]

const metaSaveLabel = computed(() => ({
  idle: '更改后自动保存', dirty: '等待保存', saving: '正在保存', saved: '已自动保存', error: '保存失败'
})[metaSaveState.value])

const editDirty = computed(() => Boolean(editing.value) && serializeEdit(editing.value!) !== originalEdit.value)
const nextPendingCount = computed(() => {
  if (!editMeta.value || !editing.value) return 0
  return editMeta.value.items.filter(item => item.review_status === 'pending' && item.id !== editing.value?.id).length
})

function currentStation() {
  return batch.value?.stations.find(station => station.station_meta_id === activeStation.value) || null
}

function syncStationForm(station: StationDetail) {
  metaForm.duty_leader = station.duty_leader || ''
  metaForm.temp_leader = station.temp_leader || '无'
  operatorsList.value = [...(station.operators || [])]
  metaSaveState.value = 'idle'
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const detail = await api.batchDetail(batchId)
    batch.value = detail
    if (!detail.stations.some(station => station.station_meta_id === activeStation.value)) {
      activeStation.value = detail.stations[0]?.station_meta_id || ''
    }
    const station = currentStation()
    if (station) syncStationForm(station)
  } catch (error: any) {
    ElMessage.error(friendlyError(error, '交接班详情加载失败'))
  } finally {
    if (!silent) loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  await load(true)
  refreshing.value = false
  ElMessage.success('数据已刷新')
}

async function loadStaff(stationCode?: string) {
  try {
    staffList.value = await api.staff(stationCode)
  } catch {
    staffList.value = []
  }
}

watch(activeStation, async () => {
  if (metaSaveTimer) clearTimeout(metaSaveTimer)
  const station = currentStation()
  if (!station) return
  syncStationForm(station)
  itemFilter.value = pendingCount(station) ? 'pending' : 'all'
  itemKeyword.value = ''
  await loadStaff(station.station_code)
})

function pendingCount(station: StationDetail) {
  return station.items.filter(item => item.review_status === 'pending').length
}

function reviewedCount(station: StationDetail) {
  return station.items.length - pendingCount(station)
}

function stationProgress(station: StationDetail) {
  return station.items.length ? Math.round(reviewedCount(station) / station.items.length * 100) : 100
}

function attentionCount(station: StationDetail) {
  return station.items.filter(item => item.priority === 'urgent' || item.priority === 'important').length
}

function overdueCount(station: StationDetail) {
  return [...station.general.monthly, ...station.general.quarterly, ...station.general.yearly]
    .filter(item => item.overdue).length
}

function sectionId(section: string, stationId = activeStation.value) {
  return `${stationId}-${section}`
}

async function scrollToSection(section: string, stationId = activeStation.value) {
  await nextTick()
  document.getElementById(sectionId(section, stationId))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function scheduleMetaSave() {
  metaSaveState.value = 'dirty'
  if (metaSaveTimer) clearTimeout(metaSaveTimer)
  metaSaveTimer = setTimeout(saveMeta, 550)
}

async function saveMeta() {
  const station = currentStation()
  if (!station) return
  const metaId = station.station_meta_id
  const payload = {
    duty_leader: metaForm.duty_leader,
    temp_leader: metaForm.temp_leader || '无',
    operators: [...operatorsList.value]
  }
  metaSaveState.value = 'saving'
  try {
    await api.patchMeta(metaId, payload)
    const local = batch.value?.stations.find(item => item.station_meta_id === metaId)
    if (local) Object.assign(local, payload)
    metaSaveState.value = 'saved'
    setTimeout(() => {
      if (metaSaveState.value === 'saved') metaSaveState.value = 'idle'
    }, 1800)
  } catch (error: any) {
    metaSaveState.value = 'error'
    ElMessage.error(friendlyError(error, '人员信息保存失败'))
  }
}

async function addDevice(station: StationDetail) {
  const content = newDeviceChange.value.trim()
  if (!content || addingDevice.value) return
  addingDevice.value = true
  try {
    const result = await api.addDeviceChange(batchId, station.station_meta_id, content)
    station.device_changes.push(result)
    newDeviceChange.value = ''
    ElMessage.success('设备变更已添加')
  } catch (error: any) {
    ElMessage.error(friendlyError(error, '添加失败'))
  } finally {
    addingDevice.value = false
  }
}

function filteredItems(station: StationDetail) {
  const key = itemKeyword.value.trim().toLowerCase()
  const priorityOrder: Record<string, number> = { urgent: 0, important: 1, normal: 2 }
  return station.items.filter(item => {
    const matchesType = itemFilter.value === 'all'
      || (itemFilter.value === 'pending' && item.review_status === 'pending')
      || (itemFilter.value === 'attention' && ['urgent', 'important'].includes(item.priority))
      || (itemFilter.value === 'completed' && item.status === 'completed')
    const matchesKey = !key || [item.title, item.latest_progress, item.previous_owner, item.next_owner]
      .join(' ').toLowerCase().includes(key)
    return matchesType && matchesKey
  }).sort((a, b) => {
    const reviewOrder = Number(a.review_status !== 'pending') - Number(b.review_status !== 'pending')
    return reviewOrder || (priorityOrder[a.priority] ?? 3) - (priorityOrder[b.priority] ?? 3)
  })
}

function reviewTagType(status: string) {
  return status === 'approved' ? 'success' : status === 'edited' ? 'primary' : status === 'rejected' ? 'danger' : 'warning'
}

function workStatusLabel(status: string) {
  return ({ completed: '已完成', in_progress: '进行中', blocked: '受阻', pending: '待启动', unknown: '未知' } as Record<string, string>)[status] || status
}

async function quickApprove(station: StationDetail, item: HandoverItemView) {
  if (approvingId.value) return
  approvingId.value = item.id
  try {
    const result = await api.approveItem(item.id, item.revision)
    item.revision = result.revision
    item.review_status = result.review_status
    ElMessage.success('已确认')
    if (!pendingCount(station)) itemFilter.value = 'all'
  } catch (error: any) {
    handleConflict(error)
  } finally {
    approvingId.value = ''
  }
}

async function approveAll(station: StationDetail) {
  bulkApproving.value = true
  try {
    const result = await api.approveAll(batchId, station.station_meta_id)
    station.items.forEach(item => {
      if (item.review_status === 'pending') {
        item.review_status = 'approved'
        item.revision += 1
      }
    })
    itemFilter.value = 'all'
    ElMessage.success(`已确认 ${result.approved} 条事项`)
  } catch (error: any) {
    ElMessage.error(friendlyError(error, '批量确认失败'))
  } finally {
    bulkApproving.value = false
  }
}

function editPayload(item: HandoverItemView) {
  return {
    title_snapshot: item.title,
    status: item.status,
    priority: item.priority,
    latest_progress: item.latest_progress,
    blocker: item.blocker,
    next_action: item.next_action,
    previous_owner: item.previous_owner,
    next_owner: item.next_owner,
    start_date: item.start_date,
    end_date: item.end_date
  }
}

function serializeEdit(item: HandoverItemView) {
  return JSON.stringify(editPayload(item))
}

async function openEdit(station: StationDetail, item: HandoverItemView) {
  editMeta.value = station
  editing.value = { ...item }
  originalEdit.value = serializeEdit(editing.value)
  drawer.value = true
  sources.value = []
  sourcesLoading.value = true
  try {
    sources.value = await api.itemSources(item.work_item_id)
  } catch {
    ElMessage.warning('来源记录暂时无法加载，不影响复核')
  } finally {
    sourcesLoading.value = false
  }
}

function updateLocalItem(result: any, reviewed = false) {
  if (!editing.value || !editMeta.value) return
  editing.value.revision = result.revision
  editing.value.review_status = result.review_status
  if (result.human_edited !== undefined) editing.value.human_edited = result.human_edited
  const local = editMeta.value.items.find(item => item.id === editing.value?.id)
  if (local) {
    Object.assign(local, editing.value, {
      color: editing.value.priority === 'urgent' ? 'red' : editing.value.priority === 'important' ? 'yellow' : 'white',
      section: editing.value.priority === 'urgent' || editing.value.priority === 'important' ? 'important' : 'handover'
    })
    if (reviewed) local.review_status = 'approved'
  }
  originalEdit.value = serializeEdit(editing.value)
}

async function saveEdit() {
  if (!editing.value || !editDirty.value) return
  saving.value = true
  try {
    const result = await api.patchItem(editing.value.id, editing.value.revision, editPayload(editing.value))
    updateLocalItem(result)
    ElMessage.success('修改已保存')
  } catch (error: any) {
    handleConflict(error)
  } finally {
    saving.value = false
  }
}

async function saveAndApprove() {
  if (!editing.value || saving.value) return
  saving.value = true
  const currentId = editing.value.id
  const station = editMeta.value
  try {
    const fields = editDirty.value ? editPayload(editing.value) : {}
    const result = await api.reviewItem(editing.value.id, editing.value.revision, fields)
    updateLocalItem(result, true)
    const next = station?.items.find(item => item.review_status === 'pending' && item.id !== currentId)
    if (next && station) {
      ElMessage.success('已确认，继续下一条')
      await openEdit(station, next)
    } else {
      ElMessage.success('已确认，本场站复核完成')
      bypassCloseGuard = true
      drawer.value = false
      itemFilter.value = 'all'
      bypassCloseGuard = false
    }
  } catch (error: any) {
    handleConflict(error)
  } finally {
    saving.value = false
  }
}

async function beforeDrawerClose(done: () => void) {
  if (bypassCloseGuard || !editDirty.value) {
    done()
    return
  }
  try {
    await ElMessageBox.confirm('还有未保存的修改，确定关闭吗？', '提示', {
      confirmButtonText: '放弃修改', cancelButtonText: '继续编辑', type: 'warning'
    })
    done()
  } catch {
    // 用户继续编辑
  }
}

function closeDrawer() {
  drawer.value = false
}

function handleConflict(error: any) {
  const detail = error?.response?.data?.detail
  if (error?.response?.status === 409) {
    ElMessage.error(typeof detail === 'object' ? detail.message : '内容已在别处修改，正在刷新')
    drawer.value = false
    load(true)
  } else {
    ElMessage.error(friendlyError(error, '操作失败'))
  }
}

function periodicRows(station: StationDetail, key: 'monthly' | 'quarterly' | 'yearly') {
  return station.general[key]
}

function rowStyle({ row }: { row: GeneralItemView }) {
  return { background: COLOR_HEX[row.color] || '#fff' }
}

function toggleGeneral(row: GeneralItemView, completed: boolean) {
  saveGeneral(row, { status: completed ? 'completed' : 'pending' })
}

async function saveGeneral(row: GeneralItemView, fields: Record<string, unknown>) {
  const nextSet = new Set(generalSavingIds.value)
  nextSet.add(row.id)
  generalSavingIds.value = nextSet
  try {
    const result = await api.patchGeneralItem(row.id, row.revision, fields)
    Object.assign(row, fields, { revision: result.revision })
    if ('status' in fields) {
      row.status = fields.status as string
      row.overdue = row.status !== 'completed' && Boolean(row.plan_end) && row.plan_end! < (batch.value?.handover_date || '')
      row.color = row.status === 'completed' ? 'green' : row.overdue ? 'red' : 'white'
    }
  } catch (error: any) {
    handleConflict(error)
  } finally {
    const doneSet = new Set(generalSavingIds.value)
    doneSet.delete(row.id)
    generalSavingIds.value = doneSet
  }
}

async function render(station: StationDetail) {
  rendering.value = true
  try {
    const result = await api.render(batchId, station.station_meta_id)
    ElMessage.success(`正式 Word V${padVersion(result.version)} 已生成，正在下载`)
    const link = document.createElement('a')
    link.href = api.downloadUrl(result.snapshot_id)
    link.download = ''
    document.body.appendChild(link)
    link.click()
    link.remove()
    await load(true)
  } catch (error: any) {
    ElMessage.error(friendlyError(error, 'Word 生成失败'))
  } finally {
    rendering.value = false
  }
}

function padVersion(version: number) {
  return String(version).padStart(3, '0')
}

function friendlyError(error: any, fallback: string) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return error?.message || fallback
}

function handleKeyboard(event: KeyboardEvent) {
  if (drawer.value && (event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    saveAndApprove()
  }
}

onMounted(async () => {
  window.addEventListener('keydown', handleKeyboard)
  await load()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeyboard)
  if (metaSaveTimer) clearTimeout(metaSaveTimer)
})
</script>

<style scoped>
.review-page {
  min-height: 480px;
}

.review-topbar {
  margin-bottom: 16px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
}

.back-button {
  height: auto;
  margin-bottom: 8px;
  padding: 0;
  color: #60758d;
}

.review-topbar h1 {
  margin: 0;
  color: #18324f;
  font-size: clamp(22px, 3vw, 29px);
  letter-spacing: 0.01em;
}

.review-topbar p {
  margin: 7px 0 0;
  color: #7b899a;
  font-size: 12px;
}

.top-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}

.station-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
  padding: 0 16px;
  border: 1px solid #e3eaf2;
  border-radius: 13px;
  background: #fff;
  box-shadow: 0 5px 16px rgba(31, 61, 95, 0.04);
}

.station-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.station-tabs :deep(.el-tabs__item) {
  height: 54px;
  font-weight: 650;
}

.station-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.tab-pending,
.tab-done {
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  display: inline-grid;
  place-items: center;
  border-radius: 999px;
  font-size: 10px;
  line-height: 1;
}

.tab-pending { color: #a05f0a; background: #fff0d4; }
.tab-done { color: #218159; background: #e3f7ee; }

.overview-card {
  margin-bottom: 16px;
  padding: 18px 21px;
  display: grid;
  grid-template-columns: minmax(250px, 1.1fr) minmax(300px, 1fr) auto;
  align-items: center;
  gap: 24px;
  border: 1px solid #dfe8f2;
  border-radius: 15px;
  background: linear-gradient(105deg, #fff 0%, #f7fbff 100%);
  box-shadow: 0 7px 21px rgba(25, 62, 102, 0.05);
}

.progress-block {
  display: flex;
  align-items: center;
  gap: 15px;
}

.progress-block > div {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.progress-block strong {
  color: #24425f;
  font-size: 14px;
}

.progress-block span {
  color: #8794a4;
  font-size: 11px;
}

.overview-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-left: 1px solid #e8edf3;
  border-right: 1px solid #e8edf3;
}

.overview-stats > div {
  padding: 4px 14px;
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 4px;
  text-align: center;
}

.overview-stats strong {
  color: #304a65;
  font-size: 20px;
}

.overview-stats span {
  color: #8a96a5;
  font-size: 10px;
}

.danger-text { color: #c54b4b !important; }
.warning-text { color: #c07a18 !important; }

.section-nav {
  max-width: 230px;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.section-nav button {
  padding: 6px 9px;
  color: #526d89;
  border: 1px solid #dae5f0;
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
  font-size: 10px;
}

.section-nav button:hover,
.section-nav .primary-nav {
  color: #fff;
  border-color: #2870b9;
  background: #2870b9;
}

.section-card {
  margin-bottom: 16px;
  scroll-margin-top: 88px;
  border: 1px solid #e1e8f0;
  border-radius: 14px;
  box-shadow: 0 6px 20px rgba(27, 57, 91, 0.045);
}

.section-card :deep(.el-card__header) {
  padding: 17px 20px;
  border-bottom-color: #edf1f5;
}

.section-card :deep(.el-card__body) {
  padding: 20px;
}

.card-heading,
.card-heading > div:first-child {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-heading > div:first-child {
  justify-content: flex-start;
}

.card-heading > div:first-child > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.card-heading strong {
  color: #283f58;
  font-size: 15px;
}

.card-heading small {
  color: #94a0ae;
  font-size: 10px;
  font-weight: 400;
}

.section-index {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  color: #356fa8;
  border-radius: 9px;
  background: #edf5fd;
  font-size: 11px;
  font-weight: 800;
}

.save-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #8b97a5;
  font-size: 10px;
}

.save-indicator i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #a9b3bf;
}

.save-indicator.saving i,
.save-indicator.dirty i { background: #e7a43d; }
.save-indicator.saved i { background: #36aa78; }
.save-indicator.error i { background: #d75b5b; }

.staff-form {
  display: grid;
  grid-template-columns: 1fr 1fr 1.5fr;
  gap: 15px;
}

.staff-form :deep(.el-form-item) { margin-bottom: 0; }
.staff-form :deep(.el-select) { width: 100%; }

.empty-line {
  padding: 10px 12px;
  color: #929dac;
  border-radius: 8px;
  background: #f7f9fb;
  font-size: 12px;
}

.device-list {
  margin-bottom: 13px;
  display: grid;
  gap: 7px;
}

.device-item {
  padding: 10px 12px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1px solid #e6ebf1;
  border-radius: 9px;
  background: #fbfcfd;
}

.device-item > span {
  width: 21px;
  height: 21px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  color: #5e7994;
  border-radius: 6px;
  background: #e8f0f8;
  font-size: 10px;
}

.device-item p {
  margin: 1px 0 0;
  color: #41566d;
  font-size: 12px;
  line-height: 1.55;
}

.add-device-row {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}

.item-tools {
  margin-bottom: 14px;
  padding: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-radius: 10px;
  background: #f6f8fb;
}

.item-search { width: 300px; }

.item-list {
  display: grid;
  gap: 9px;
}

.item-card {
  position: relative;
  overflow: hidden;
  padding: 14px 15px 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border: 1px solid #e2e8ef;
  border-radius: 11px;
  background: #fff;
  cursor: pointer;
  transition: 0.16s ease;
}

.item-card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: #b7c4d1;
}

.item-card.priority-important { background: #fffdf0; border-color: #f1e5aa; }
.item-card.priority-important::before { background: #dfb62b; }
.item-card.priority-urgent { background: #fff3f3; border-color: #efcccc; }
.item-card.priority-urgent::before { background: #dd5555; }
.item-card.reviewed { opacity: 0.82; }

.item-card:hover {
  border-color: #9dbfe1;
  box-shadow: 0 5px 15px rgba(36, 78, 119, 0.08);
  transform: translateY(-1px);
}

.item-main { min-width: 0; flex: 1; }

.item-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.item-title-row h3 {
  margin: 1px 0 0;
  color: #2d435b;
  font-size: 13px;
  line-height: 1.5;
}

.item-tags {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  gap: 4px;
}

.item-progress {
  margin: 8px 0 0;
  color: #52657a;
  font-size: 12px;
  line-height: 1.6;
}

.item-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px 14px;
  color: #919cab;
  font-size: 10px;
}

.item-action { flex: 0 0 auto; }

.periodic-tabs { border-color: #e1e7ee; }
.periodic-table :deep(.el-table__header th) { background: #f5f7fa; color: #66768a; }
.periodic-title { color: #334b63; font-size: 12px; line-height: 1.5; }
.muted { color: #99a4b1; font-size: 10px; }

.publish-card :deep(.el-card__body) { padding-top: 17px; }

.publish-panel {
  padding: 17px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border: 1px solid #f0d9a9;
  border-radius: 12px;
  background: #fffaf0;
}

.publish-panel.ready {
  border-color: #bfe3d2;
  background: #f1fbf7;
}

.publish-state {
  display: flex;
  align-items: center;
  gap: 13px;
}

.publish-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  color: #b06a12;
  border-radius: 12px;
  background: #ffedc7;
  font-size: 17px;
  font-weight: 800;
}

.ready .publish-icon { color: #22805b; background: #d9f3e8; }
.publish-state strong { color: #3c4e61; font-size: 14px; }
.publish-state p { margin: 4px 0 0; color: #7c8998; font-size: 11px; }

.version-area { margin-top: 16px; }

.latest-version {
  padding: 13px 16px;
  display: flex;
  align-items: center;
  gap: 18px;
  border: 1px solid #e2e9f0;
  border-radius: 10px;
  background: #f8fafc;
}

.latest-version > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.latest-version span,
.latest-meta { color: #8793a2; font-size: 10px; }
.latest-version strong { color: #2868a6; font-size: 18px; }
.latest-meta { flex: 1; }
.version-history { margin-top: 10px; border-top: 0; }

.drawer-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.drawer-title span { color: #263d55; font-size: 18px; font-weight: 700; }
.drawer-title small { color: #8a96a4; font-size: 11px; }

.edit-form { margin-top: 18px; }
.edit-form :deep(.el-select),
.edit-form :deep(.el-date-editor) { width: 100%; }
.edit-form :deep(.el-form-item) { margin-bottom: 16px; }

.form-grid {
  display: grid;
  gap: 14px;
}

.form-grid.two { grid-template-columns: 1fr 1fr; }
.source-collapse { margin-top: 2px; }

.source-row {
  padding: 10px 0;
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px;
  border-bottom: 1px dashed #e4e9ef;
}

.source-row > div { display: flex; flex-direction: column; gap: 3px; }
.source-row strong { color: #3470aa; font-size: 11px; }
.source-row small { color: #9ba5b1; font-size: 9px; }
.source-row p { margin: 0; color: #4a5e73; font-size: 11px; line-height: 1.6; }

.drawer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.keyboard-tip { color: #9aa4b0; font-size: 10px; }
.drawer-footer > div { display: flex; gap: 7px; }

@media (max-width: 1100px) {
  .overview-card {
    grid-template-columns: 1fr 1fr;
  }

  .section-nav {
    max-width: none;
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}

@media (max-width: 760px) {
  .review-topbar {
    align-items: stretch;
    flex-direction: column;
  }

  .top-actions { width: 100%; }
  .top-actions :deep(.el-button) { flex: 1; margin: 0; }

  .overview-card {
    grid-template-columns: 1fr;
    gap: 16px;
  }

  .overview-stats {
    padding: 12px 0;
    border: 0;
    border-top: 1px solid #e8edf3;
    border-bottom: 1px solid #e8edf3;
  }

  .section-card :deep(.el-card__header),
  .section-card :deep(.el-card__body) { padding: 15px 13px; }

  .staff-form { grid-template-columns: 1fr; }

  .items-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .item-tools {
    align-items: stretch;
    flex-direction: column;
    overflow-x: auto;
  }

  .item-tools :deep(.el-radio-group) { flex-wrap: nowrap; }
  .item-search { width: 100%; }

  .item-card {
    align-items: stretch;
    flex-direction: column;
  }

  .item-title-row { flex-direction: column; }
  .item-action :deep(.el-button) { width: 100%; }

  .publish-panel {
    align-items: stretch;
    flex-direction: column;
  }

  .latest-version { align-items: flex-start; flex-wrap: wrap; }
  .latest-meta { flex-basis: 100%; }

  .form-grid.two { grid-template-columns: 1fr; gap: 0; }
  .drawer-footer { align-items: stretch; flex-direction: column; }
  .keyboard-tip { display: none; }
  .drawer-footer > div { width: 100%; }
  .drawer-footer :deep(.el-button) { min-width: 0; flex: 1; margin: 0; }
}
</style>
