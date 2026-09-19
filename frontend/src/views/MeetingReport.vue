<template>
  <div class="meeting-page">
    <section class="page-card">
      <div class="page-head">
        <div>
          <span class="eyebrow">例会材料</span>
          <h1>月度例会工作材料</h1>
          <p>自动汇总当月各班次的重点工作、交接工作和定期工作，按“已完成 / 正在开展 / 未开展”分组，轻重缓急一目了然；可一键导出 Excel 直接上会。</p>
        </div>
        <div class="head-actions">
          <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="loadMeeting" />
          <el-button type="primary" :loading="exporting" @click="exportExcel">导出 Excel</el-button>
        </div>
      </div>

      <div v-if="meeting" class="summary-grid">
        <div class="sum-card done"><strong>{{ meeting.summary.completed }}</strong><span>已完成</span></div>
        <div class="sum-card doing"><strong>{{ meeting.summary.in_progress }}</strong><span>正在开展</span></div>
        <div class="sum-card todo"><strong>{{ meeting.summary.pending }}</strong><span>未开展</span></div>
        <div class="sum-card urgent"><strong>{{ meeting.summary.urgent }}</strong><span>其中紧急</span></div>
      </div>

      <el-skeleton v-if="loadingMeeting" :rows="6" animated />
      <template v-else-if="meeting">
        <section v-for="group in meeting.groups" :key="group.key" class="group-block">
          <h2>{{ group.label }}（{{ group.count }} 项）</h2>
          <el-table v-if="group.rows.length" :data="group.rows" size="large" class="meeting-table">
            <el-table-column type="index" label="序号" width="64" align="center" />
            <el-table-column prop="kind_label" label="类别" width="96" align="center" />
            <el-table-column prop="title" label="工作内容" min-width="260" show-overflow-tooltip />
            <el-table-column prop="station" label="场站" width="110" align="center" />
            <el-table-column label="轻重缓急" width="110" align="center">
              <template #default="{ row }">
                <el-tag :type="priorityTag(row.priority)" effect="dark" size="small">{{ row.priority_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="开展状态" width="110" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small">{{ row.status_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="owner" label="责任人" width="110" align="center" />
            <el-table-column prop="progress" label="进展 / 备注" min-width="180" show-overflow-tooltip />
            <el-table-column prop="next_action" label="下一步 / 计划" min-width="160" show-overflow-tooltip />
          </el-table>
          <el-empty v-else description="本组暂无事项" :image-size="60" />
        </section>
      </template>
    </section>

    <section class="page-card">
      <div class="page-head">
        <div>
          <span class="eyebrow">全年定期工作</span>
          <h1>{{ year }} 年年度定期工作一览</h1>
          <p>33 项年度定期工作的全年开展台账；“上次记录”自动带出往年最近一次填写内容，录入本年情况时直接参考，不用翻历史交接班。</p>
        </div>
        <div class="head-actions">
          <el-date-picker v-model="yearDate" type="year" value-format="YYYY" :clearable="false" @change="loadYearly" />
        </div>
      </div>

      <div v-if="yearly" class="summary-grid">
        <div class="sum-card done"><strong>{{ yearly.summary.completed }}</strong><span>已开展</span></div>
        <div class="sum-card doing"><strong>{{ yearly.summary.in_progress }}</strong><span>正在开展</span></div>
        <div class="sum-card todo"><strong>{{ yearly.summary.pending }}</strong><span>未开展</span></div>
        <div class="sum-card"><strong>{{ yearly.summary.total }}</strong><span>全年总数</span></div>
      </div>

      <el-skeleton v-if="loadingYearly" :rows="6" animated />
      <el-table v-else-if="yearly" :data="yearly.items" size="large" class="meeting-table" max-height="560">
        <el-table-column type="index" label="序号" width="64" align="center" />
        <el-table-column prop="name" label="工作项目" min-width="170" show-overflow-tooltip />
        <el-table-column prop="schedule" label="计划时间" width="130" align="center" show-overflow-tooltip />
        <el-table-column label="开展状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.state === 'completed' ? 'success' : row.state === 'in_progress' ? 'primary' : 'info'" size="small">
              {{ row.state_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="owner" label="责任人" width="110" align="center" />
        <el-table-column label="本年最近记录" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.latest">{{ row.latest.status_label }}<template v-if="row.latest.owner"> · {{ row.latest.owner }}</template><template v-if="row.latest.note"> · {{ row.latest.note }}</template></span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="上次记录（往年记忆）" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.memory" class="memory">{{ row.memory.status_label }}<template v-if="row.memory.owner"> · {{ row.memory.owner }}</template><template v-if="row.memory.note"> · {{ row.memory.note }}</template></span>
            <span v-else class="muted">首次开展</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type MeetingReport, type YearlyPlanReport } from '@/api'

const now = new Date()
const month = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const yearDate = ref(String(now.getFullYear()))
const year = ref(now.getFullYear())
const meeting = ref<MeetingReport | null>(null)
const yearly = ref<YearlyPlanReport | null>(null)
const loadingMeeting = ref(false)
const loadingYearly = ref(false)
const exporting = ref(false)

function priorityTag(priority: string) {
  if (priority === 'urgent') return 'danger'
  if (priority === 'important') return 'warning'
  return 'info'
}
function statusTag(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'in_progress') return 'primary'
  if (status === 'blocked') return 'danger'
  return 'info'
}

async function loadMeeting() {
  if (!month.value) return
  loadingMeeting.value = true
  try {
    meeting.value = await api.monthlyMeeting(month.value)
  } catch {
    ElMessage.error('例会材料加载失败，请稍后重试。')
  } finally { loadingMeeting.value = false }
}

async function loadYearly() {
  year.value = Number(yearDate.value) || now.getFullYear()
  loadingYearly.value = true
  try {
    yearly.value = await api.yearlyPlan(year.value)
  } catch {
    ElMessage.error('年度定期工作一览加载失败，请稍后重试。')
  } finally { loadingYearly.value = false }
}

async function exportExcel() {
  if (!month.value) return
  exporting.value = true
  try {
    await api.exportMonthlyMeeting(month.value)
    ElMessage.success('Excel 已开始下载')
  } catch {
    ElMessage.error('导出失败，请稍后重试。')
  } finally { exporting.value = false }
}

onMounted(() => { loadMeeting(); loadYearly() })
</script>

<style scoped>
.meeting-page {
  width: min(1380px, 100%);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.page-card {
  padding: 22px 24px;
  border: 1px solid #e2e9f2;
  border-radius: 14px;
  background: #fff;
}
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.page-head h1 { margin: 2px 0 6px; font-size: 20px; color: #1d3550; }
.page-head p { margin: 0; max-width: 720px; color: #63778a; font-size: 13px; line-height: 1.7; }
.eyebrow { color: #2265aa; font-size: 12px; font-weight: 600; letter-spacing: 2px; }
.head-actions { display: flex; gap: 10px; align-items: center; }

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.sum-card {
  padding: 12px 16px;
  border-radius: 10px;
  background: #f4f7fb;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.sum-card strong { font-size: 24px; color: #1d3550; }
.sum-card span { color: #63778a; font-size: 12px; }
.sum-card.done { background: #e9f6ec; }
.sum-card.doing { background: #e5effa; }
.sum-card.todo { background: #f2f2f2; }
.sum-card.urgent { background: #fdeaea; }

.group-block { margin-top: 14px; }
.group-block h2 { margin: 0 0 8px; font-size: 15px; color: #1d3550; }
.meeting-table { width: 100%; }
.muted { color: #a0aec0; }
.memory { color: #2265aa; }
</style>
