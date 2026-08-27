<template>
  <div v-loading="loading">
    <div class="toolbar" v-if="batch">
      <div>
        <el-button link @click="$router.push('/')">← 返回列表</el-button>
        <h2 class="inline-title">
          {{ cnDate(batch.start_date) }} ~ {{ cnDate(batch.end_date) }} 交接班复核
        </h2>
      </div>
    </div>

    <el-tabs v-if="batch" v-model="activeStation">
      <el-tab-pane
        v-for="st in batch.stations"
        :key="st.station_meta_id"
        :label="st.station_name"
        :name="st.station_meta_id"
      >
        <!-- 基本信息 -->
        <el-card class="section" shadow="never">
          <template #header>一、基本信息（值班人员）</template>
          <el-form inline>
            <el-form-item label="值班负责人">
              <el-select v-model="metaForm.duty_leader" filterable allow-create
                         default-first-option style="width: 160px">
                <el-option v-for="s in staffList" :key="s.id"
                           :label="`${s.name}（${s.role}）`" :value="s.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="临时值班负责人">
              <el-select v-model="metaForm.temp_leader" filterable allow-create
                         default-first-option style="width: 160px">
                <el-option label="无" value="无" />
                <el-option v-for="s in staffList" :key="s.id"
                           :label="`${s.name}（${s.role}）`" :value="s.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="当班值班员">
              <el-select v-model="operatorsList" multiple filterable allow-create
                         default-first-option style="width: 300px">
                <el-option v-for="s in staffList" :key="s.id"
                           :label="`${s.name}（${s.role}）`" :value="s.name" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" plain @click="saveMeta">保存人员信息</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 设备变更 -->
        <el-card class="section" shadow="never">
          <template #header>二、设备变更情况</template>
          <div v-if="!st.device_changes.length" class="muted">本班无设备变更</div>
          <div v-for="d in st.device_changes" :key="d.id" class="device-row">
            {{ d.content }}
          </div>
          <div class="add-row">
            <el-input v-model="newDeviceChange" placeholder="新增设备变更描述" style="width: 400px" />
            <el-button @click="addDevice(st)">添加</el-button>
          </div>
        </el-card>

        <!-- 专业工作事项 -->
        <el-card class="section" shadow="never">
          <template #header>
            三 / 四、专业工作事项（点击卡片复核编辑）
            <el-tag class="pending-tag" v-if="pendingCount(st) > 0" type="warning">
              待复核 {{ pendingCount(st) }}
            </el-tag>
            <el-button class="ml8" size="small" v-if="pendingCount(st) > 0"
                       @click="approveAll(st)">一键全部确认</el-button>
          </template>
          <div
            v-for="it in st.items"
            :key="it.id"
            class="item-card"
            :style="{ background: COLOR_HEX[it.color] || '#fff' }"
            @click="openEdit(st, it)"
          >
            <div class="item-head">
              <span class="item-title">{{ it.title }}</span>
              <span class="item-tags">
                <el-tag size="small" :type="reviewTagType(it.review_status)">
                  {{ STATUS_LABEL[it.review_status] }}
                </el-tag>
                <el-tag size="small" type="danger" v-if="it.priority === 'urgent'">紧急</el-tag>
                <el-tag size="small" type="warning" v-else-if="it.priority === 'important'">重点</el-tag>
                <el-tag size="small" :type="it.status === 'completed' ? 'success' : 'info'">
                  {{ it.status === 'completed' ? '已完成' : '未完成' }}
                </el-tag>
                <el-tag size="small">{{ it.section === 'important' ? '重点工作节' : '需交接工作节' }}</el-tag>
              </span>
            </div>
            <div class="item-meta">
              {{ cnDate(it.start_date) }} ~ {{ cnDate(it.end_date) }} ·
              {{ it.previous_owner || '—' }} → {{ it.next_owner || '—' }}
            </div>
            <div class="item-progress" v-if="it.latest_progress">{{ it.latest_progress }}</div>
          </div>
        </el-card>

        <!-- 定期工作 -->
        <el-card class="section" shadow="never">
          <template #header>
            六、定期工作完成情况（内置模板库自动生成，颜色/排序由程序计算；可在线匹配实际完成情况）
          </template>
          <template v-for="sec in [
            { key: 'monthly', title: '6.1 月度定期工作' },
            { key: 'quarterly', title: '6.2 季度定期工作' },
            { key: 'yearly', title: '6.3 年度定期工作' }
          ]" :key="sec.key">
            <h4>{{ sec.title }}（{{ (st.general as any)[sec.key].length }} 项）</h4>
            <el-table :data="(st.general as any)[sec.key]" border size="small"
                      :row-style="rowStyle">
              <el-table-column label="工作内容" min-width="240">
                <template #default="{ row }">
                  <el-tooltip v-if="row.template_meta && row.template_meta.content"
                              :content="row.template_meta.content" placement="top">
                    <span>{{ row.title }}</span>
                  </el-tooltip>
                  <span v-else>{{ row.title }}</span>
                </template>
              </el-table-column>
              <el-table-column label="截止" width="110" align="center">
                <template #default="{ row }">{{ cnDate(row.plan_end) }}</template>
              </el-table-column>
              <el-table-column label="完成情况" width="130" align="center">
                <template #default="{ row }">
                  <el-select :model-value="row.status" size="small" style="width: 100px"
                             @change="(v: string) => saveGeneral(row, { status: v })">
                    <el-option label="未完成" value="pending" />
                    <el-option label="已完成" value="completed" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="完成人" width="160">
                <template #default="{ row }">
                  <el-select :model-value="row.owner" size="small" filterable clearable
                             allow-create default-first-option style="width: 130px"
                             @change="(v: string) => saveGeneral(row, { owner: v || '' })">
                    <el-option v-for="s in staffList" :key="s.id" :label="s.name" :value="s.name" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="超期" width="80" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.overdue" type="danger" size="small">超期</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-card>

        <!-- 发布 -->
        <el-card class="section" shadow="never">
          <template #header>生成正式 Word</template>
          <div class="publish-bar">
            <el-button
              type="primary"
              :disabled="pendingCount(st) > 0"
              :loading="rendering"
              @click="render(st)"
            >
              生成正式 Word
            </el-button>
            <span class="muted" v-if="pendingCount(st) > 0">
              仍有 {{ pendingCount(st) }} 条事项待复核，全部确认后才能生成
            </span>
          </div>
          <el-table v-if="st.snapshots.length" :data="st.snapshots" border size="small" class="mt8">
            <el-table-column label="版本" width="80" align="center">
              <template #default="{ row }">V{{ String(row.version).padStart(3, '0') }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="生成时间" width="200" />
            <el-table-column prop="docx_path" label="文件路径" min-width="300" show-overflow-tooltip />
            <el-table-column label="下载" width="90" align="center">
              <template #default="{ row }">
                <el-link type="primary" :href="api.downloadUrl(row.id)" target="_blank">
                  下载
                </el-link>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 复核编辑抽屉 -->
    <el-drawer v-model="drawer" title="事项复核与编辑" size="560px">
      <template v-if="editing">
        <el-form label-width="110px">
          <el-form-item label="标题">
            <el-input v-model="editing.title" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="editing.status">
              <el-option label="进行中" value="in_progress" />
              <el-option label="已完成" value="completed" />
              <el-option label="受阻" value="blocked" />
              <el-option label="待启动" value="pending" />
            </el-select>
          </el-form-item>
          <el-form-item label="优先级">
            <el-select v-model="editing.priority">
              <el-option label="紧急（红）" value="urgent" />
              <el-option label="重点（黄）" value="important" />
              <el-option label="普通（白）" value="normal" />
            </el-select>
          </el-form-item>
          <el-form-item label="最新进展">
            <el-input v-model="editing.latest_progress" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item label="受阻原因">
            <el-input v-model="editing.blocker" />
          </el-form-item>
          <el-form-item label="下一步">
            <el-input v-model="editing.next_action" />
          </el-form-item>
          <el-form-item label="交接前责任人">
            <el-input v-model="editing.previous_owner" />
          </el-form-item>
          <el-form-item label="交接后责任人">
            <el-input v-model="editing.next_owner" />
          </el-form-item>
          <el-form-item label="开始/结束日期">
            <el-date-picker v-model="editing.start_date" type="date" value-format="YYYY-MM-DD" />
            <span class="ml8 mr8">~</span>
            <el-date-picker v-model="editing.end_date" type="date" value-format="YYYY-MM-DD" />
          </el-form-item>
        </el-form>

        <el-divider>原始证据（来源记录）</el-divider>
        <div v-loading="sourcesLoading">
          <div v-for="(s, i) in sources" :key="i" class="source-row">
            <span class="source-date">{{ cnDate(s.date) }}</span>
            <span>{{ s.text }}</span>
          </div>
          <div v-if="!sources.length" class="muted">无来源记录</div>
        </div>

        <div class="drawer-footer">
          <el-button @click="saveEdit" :loading="saving">保存修改</el-button>
          <el-button type="success" @click="approve" :loading="saving">确认无误</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  api, cnDate, COLOR_HEX, STATUS_LABEL,
  type BatchDetail, type GeneralItemView, type HandoverItemView,
  type SourceRow, type Staff, type StationDetail
} from '@/api'

const route = useRoute()
const batchId = route.params.id as string

const loading = ref(false)
const batch = ref<BatchDetail | null>(null)
const activeStation = ref('')
const rendering = ref(false)

// 抽屉编辑
const drawer = ref(false)
const editing = ref<HandoverItemView | null>(null)
const editMeta = ref<StationDetail | null>(null)
const sources = ref<SourceRow[]>([])
const sourcesLoading = ref(false)
const saving = ref(false)

// 基本信息表单（随场站切换刷新）
const metaForm = reactive({ duty_leader: '', temp_leader: '无' })
const operatorsList = ref<string[]>([])
const staffList = ref<Staff[]>([])
const newDeviceChange = ref('')

function currentStation(): StationDetail | null {
  return batch.value?.stations.find(s => s.station_meta_id === activeStation.value) || null
}

watch(activeStation, () => {
  const st = currentStation()
  if (st) {
    metaForm.duty_leader = st.duty_leader
    metaForm.temp_leader = st.temp_leader
    operatorsList.value = [...(st.operators || [])]
  }
})

async function load() {
  loading.value = true
  try {
    batch.value = await api.batchDetail(batchId)
    if (!activeStation.value && batch.value.stations.length) {
      activeStation.value = batch.value.stations[0].station_meta_id
    }
  } finally {
    loading.value = false
  }
}

async function loadStaff() {
  try {
    staffList.value = await api.staff()
  } catch {
    staffList.value = []
  }
}

function pendingCount(st: StationDetail) {
  return st.items.filter(i => i.review_status === 'pending').length
}

function reviewTagType(status: string) {
  return status === 'approved' ? 'success'
    : status === 'edited' ? 'primary'
    : status === 'rejected' ? 'danger' : 'warning'
}

function rowStyle({ row }: { row: { color: string } }) {
  return { background: COLOR_HEX[row.color] || '#fff' }
}

async function openEdit(st: StationDetail, it: HandoverItemView) {
  editMeta.value = st
  editing.value = { ...it }
  drawer.value = true
  sourcesLoading.value = true
  try {
    sources.value = await api.itemSources(it.work_item_id)
  } finally {
    sourcesLoading.value = false
  }
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  try {
    const r = await api.patchItem(editing.value.id, editing.value.revision, {
      title_snapshot: editing.value.title,
      status: editing.value.status,
      priority: editing.value.priority,
      latest_progress: editing.value.latest_progress,
      blocker: editing.value.blocker,
      next_action: editing.value.next_action,
      previous_owner: editing.value.previous_owner,
      next_owner: editing.value.next_owner,
      start_date: editing.value.start_date,
      end_date: editing.value.end_date
    })
    editing.value.revision = r.revision
    editing.value.review_status = r.review_status
    ElMessage.success('已保存（状态：已编辑）')
    await load()
  } catch (e: any) {
    handleConflict(e)
  } finally {
    saving.value = false
  }
}

async function approve() {
  if (!editing.value) return
  saving.value = true
  try {
    const r = await api.approveItem(editing.value.id, editing.value.revision)
    ElMessage.success('已确认')
    drawer.value = false
    await load()
  } catch (e: any) {
    handleConflict(e)
  } finally {
    saving.value = false
  }
}

async function approveAll(st: StationDetail) {
  for (const it of st.items.filter(i => i.review_status === 'pending')) {
    await api.approveItem(it.id, it.revision)
  }
  ElMessage.success('已全部确认')
  await load()
}

function handleConflict(e: any) {
  const detail = e?.response?.data?.detail
  if (e?.response?.status === 409) {
    ElMessage.error(typeof detail === 'object' ? detail.message : '版本冲突，已刷新')
    load()
  } else {
    ElMessage.error(typeof detail === 'string' ? detail : '操作失败')
  }
}

async function saveMeta() {
  const st = currentStation()
  if (!st) return
  await api.patchMeta(st.station_meta_id, {
    duty_leader: metaForm.duty_leader,
    temp_leader: metaForm.temp_leader,
    operators: operatorsList.value
  })
  ElMessage.success('人员信息已保存')
  await load()
}

async function saveGeneral(row: GeneralItemView, fields: Record<string, unknown>) {
  try {
    const r = await api.patchGeneralItem(row.id, row.revision, fields)
    Object.assign(row, fields, { revision: r.revision })
    // 颜色/超期由后端在下次详情返回时重算，这里先局部同步便于展示
    if ('status' in fields) {
      row.status = fields.status as string
      row.color = row.status === 'completed' ? 'green'
        : row.overdue ? 'red' : 'white'
    }
    ElMessage.success('定期工作完成情况已更新')
  } catch (e: any) {
    handleConflict(e)
  }
}

async function addDevice(st: StationDetail) {
  if (!newDeviceChange.value.trim()) return
  await api.addDeviceChange(batchId, st.station_meta_id, newDeviceChange.value.trim())
  newDeviceChange.value = ''
  await load()
}

async function render(st: StationDetail) {
  rendering.value = true
  try {
    const r = await api.render(batchId, st.station_meta_id)
    ElMessage.success(`已生成正式 Word V${String(r.version).padStart(3, '0')}`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '生成失败')
  } finally {
    rendering.value = false
  }
}

onMounted(() => {
  load()
  loadStaff()
})
</script>

<style scoped>
.toolbar {
  margin-bottom: 8px;
}
.inline-title {
  display: inline-block;
  margin: 0 0 0 8px;
  font-size: 18px;
}
.section {
  margin-bottom: 16px;
}
.pending-tag {
  margin-left: 8px;
}
.ml8 {
  margin-left: 8px;
}
.mr8 {
  margin-right: 8px;
}
.mt8 {
  margin-top: 8px;
}
.muted {
  color: #909399;
  font-size: 13px;
}
.device-row {
  margin: 4px 0;
}
.add-row {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
.item-card {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: box-shadow 0.15s;
}
.item-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
.item-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.item-title {
  font-weight: 600;
}
.item-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.item-meta {
  font-size: 12px;
  color: #606266;
  margin-top: 4px;
}
.item-progress {
  font-size: 13px;
  margin-top: 4px;
  color: #303133;
}
.source-row {
  display: flex;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed #e4e7ed;
  font-size: 13px;
}
.source-date {
  color: #409eff;
  white-space: nowrap;
}
.drawer-footer {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.publish-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
