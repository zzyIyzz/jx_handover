<template>
  <el-dialog
    :model-value="modelValue"
    title="系统管理"
    width="min(980px, calc(100vw - 24px))"
    top="5vh"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
    @opened="loadAdminData"
  >
    <div class="admin-intro">
      <div>
        <span class="admin-kicker">仅管理员可见</span>
        <h3>运行状态与数据安全</h3>
        <p>这里不会显示或传输完整 API Key；普通使用人员也看不到此入口。</p>
      </div>
      <el-button :loading="loading" @click="loadAdminData">刷新状态</el-button>
    </div>

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
      show-icon
      class="admin-alert"
    />

    <div class="admin-grid">
      <section class="admin-card">
        <div class="card-heading">
          <span class="card-icon ai">AI</span>
          <div><h4>Qwen 智能整理</h4><p>导入工作日志时辅助整理，失败会回退到本地规则。</p></div>
        </div>
        <template v-if="aiStatus">
          <dl class="status-list">
            <div><dt>运行模式</dt><dd>{{ aiStatus.mode === 'qwen' ? 'Qwen' : '本地规则' }}</dd></div>
            <div><dt>模型</dt><dd>{{ aiStatus.model || '—' }}</dd></div>
            <div><dt>API Key</dt><dd>{{ aiStatus.configured ? `已配置 ${aiStatus.key_hint || ''}` : '未配置' }}</dd></div>
          </dl>
          <div class="status-line" :class="aiStatus.configured ? 'ready' : 'warning'">
            <span></span>{{ aiStatus.configured ? '配置已就绪' : '尚未填写 Key，导入仍可使用本地规则' }}
          </div>
        </template>
        <el-skeleton v-else :rows="3" animated />
        <el-button type="primary" plain :loading="testingAi" :disabled="!aiStatus" @click="testAi">
          测试 AI 连接
        </el-button>
        <el-alert
          v-if="aiTestResult"
          :title="aiTestResult.message"
          :type="aiTestResult.ok ? 'success' : 'warning'"
          :closable="false"
          show-icon
          class="result-alert"
        />
      </section>

      <section class="admin-card">
        <div class="card-heading">
          <span class="card-icon backup">备</span>
          <div><h4>数据库备份</h4><p>先生成一致的服务器本地备份，再复制完成文件到共享盘。</p></div>
        </div>
        <div class="backup-note">
          手动备份不会中断其他人使用，也不会把正在写入的数据库直接复制到 NAS。
        </div>
        <el-button type="primary" plain :loading="backingUp" @click="backupNow">立即备份</el-button>
        <template v-if="backupResult">
          <div class="backup-result">
            <strong>备份已完成</strong>
            <span>{{ backupResult.database_file }} · {{ formatBytes(backupResult.size) }}</span>
            <span v-if="backupResult.nas_path">共享盘副本已校验完成</span>
            <span v-else-if="backupResult.nas_error" class="backup-warning">本地备份成功；共享盘复制失败：{{ backupResult.nas_error }}</span>
            <span v-else>当前未配置共享盘备份目录，本地备份已保留。</span>
          </div>
        </template>
      </section>
    </div>

    <section class="audit-card">
      <div class="audit-heading">
        <div><h4>最近操作记录</h4><p>只记录操作人、接口、结果和时间，不记录表单内容、口令或 API Key。</p></div>
        <el-tag effect="plain">最近 {{ auditRows.length }} 条</el-tag>
      </div>
      <el-table v-if="auditRows.length" :data="auditRows" max-height="330" size="small" row-key="id">
        <el-table-column label="时间" min-width="155">
          <template #default="{ row }">{{ cnDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="actor_name" label="操作人" min-width="105" />
        <el-table-column label="操作" min-width="280">
          <template #default="{ row }">{{ auditAction(row.method, row.request_path) }}</template>
        </el-table-column>
        <el-table-column label="结果" width="92" align="center">
          <template #default="{ row }">
            <el-tag :type="row.response_status < 400 ? 'success' : 'danger'" effect="light" size="small">
              {{ row.response_status < 400 ? '成功' : `失败 ${row.response_status}` }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="client_ip" label="访问电脑" min-width="125" />
      </el-table>
      <el-empty v-else-if="!loading" description="暂无可显示的操作记录" :image-size="64" />
      <el-skeleton v-else :rows="4" animated />
    </section>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  api, cnDateTime, type AiAdminStatus, type AiConnectionResult,
  type AuditEventView, type BackupResult
} from '@/api'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const loading = ref(false)
const testingAi = ref(false)
const backingUp = ref(false)
const loadError = ref('')
const aiStatus = ref<AiAdminStatus | null>(null)
const aiTestResult = ref<AiConnectionResult | null>(null)
const auditRows = ref<AuditEventView[]>([])
const backupResult = ref<BackupResult | null>(null)

async function loadAdminData() {
  loading.value = true
  loadError.value = ''
  try {
    const [status, audit] = await Promise.all([api.adminAiStatus(), api.adminAudit(30)])
    aiStatus.value = status
    auditRows.value = audit
  } catch (error: any) {
    if (error?.response?.status === 403) loadError.value = '当前身份没有管理员权限。'
    else loadError.value = error?.response?.data?.detail || '管理信息加载失败，请检查服务器连接。'
  } finally {
    loading.value = false
  }
}

async function testAi() {
  testingAi.value = true
  aiTestResult.value = null
  try {
    aiTestResult.value = await api.adminAiTest()
    if (aiTestResult.value.ok) ElMessage.success(aiTestResult.value.message)
    else ElMessage.warning(aiTestResult.value.message)
  } catch (error: any) {
    const message = error?.response?.data?.detail || error?.message || 'AI 连接测试失败'
    aiTestResult.value = { ok: false, mode: aiStatus.value?.mode || 'qwen', message }
    ElMessage.warning(`${message}；工作日志仍可使用本地规则整理。`)
  } finally {
    testingAi.value = false
  }
}

async function backupNow() {
  backingUp.value = true
  backupResult.value = null
  try {
    backupResult.value = await api.adminBackup()
    if (backupResult.value.nas_error) ElMessage.warning('本地备份成功，但共享盘复制失败；详细信息已显示。')
    else ElMessage.success('数据库备份与校验已完成')
    await loadAuditOnly()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '数据库备份失败')
  } finally {
    backingUp.value = false
  }
}

async function loadAuditOnly() {
  try { auditRows.value = await api.adminAudit(30) } catch { /* 主操作结果已经显示 */ }
}

function formatBytes(size: number) {
  if (!Number.isFinite(size)) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function auditAction(method: string, path: string) {
  const operation: Record<string, string> = { POST: '新增/执行', PATCH: '修改', DELETE: '删除', PUT: '更新' }
  const area = path.includes('/render') ? '生成 Word'
    : path.includes('/imports/') ? '导入数据'
      : path.includes('/backup') ? '创建备份'
        : path.includes('/handover-items') || path.includes('/items') ? '交接事项'
          : path.includes('/external-assessments') ? '外委考核'
            : path.includes('/device-changes') ? '设备变更'
              : path.includes('/general-items') ? '定期工作'
                : path.includes('/handover-station-meta') ? '基本信息'
                  : path.includes('/handovers') ? '班次'
                    : '系统'
  return `${operation[method] || method} · ${area}`
}
</script>

<style scoped>
.admin-intro, .audit-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.admin-intro { margin-bottom: 16px; }
.admin-kicker { color: #2d6eaa; font-size: 11px; font-weight: 800; letter-spacing: .12em; }
.admin-intro h3, .audit-heading h4 { margin: 5px 0 4px; color: #203b57; }
.admin-intro p, .audit-heading p, .card-heading p { margin: 0; color: #748599; font-size: 12px; line-height: 1.6; }
.admin-alert { margin-bottom: 14px; }
.admin-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.admin-card, .audit-card { padding: 18px; border: 1px solid #e2eaf3; border-radius: 14px; background: #fbfdff; }
.card-heading { margin-bottom: 15px; display: flex; gap: 11px; }
.card-heading h4 { margin: 1px 0 4px; color: #27415d; }
.card-icon { width: 38px; height: 38px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 11px; font-size: 12px; font-weight: 850; }
.card-icon.ai { color: #5b3fad; background: #efeafd; }
.card-icon.backup { color: #176e50; background: #e5f7ef; }
.status-list { margin: 0 0 12px; display: grid; gap: 7px; }
.status-list div { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
.status-list dt { color: #7c8b9b; }
.status-list dd { margin: 0; max-width: 70%; overflow: hidden; color: #304962; text-overflow: ellipsis; white-space: nowrap; }
.status-line { margin-bottom: 13px; display: flex; align-items: center; gap: 8px; color: #52687d; font-size: 12px; }
.status-line span { width: 8px; height: 8px; border-radius: 50%; }
.status-line.ready span { background: #31ae78; box-shadow: 0 0 0 4px #dff5ec; }
.status-line.warning span { background: #dfa139; box-shadow: 0 0 0 4px #fff0d2; }
.result-alert { margin-top: 12px; }
.backup-note { min-height: 76px; margin-bottom: 13px; padding: 12px; color: #5e7186; border-radius: 9px; background: #eef5fb; font-size: 12px; line-height: 1.65; }
.backup-result { margin-top: 12px; display: grid; gap: 4px; color: #5d7085; font-size: 11px; line-height: 1.5; }
.backup-result strong { color: #217052; font-size: 12px; }
.backup-warning { color: #a8680b; }
.audit-card { margin-top: 14px; background: #fff; }
.audit-heading { margin-bottom: 12px; }
@media (max-width: 720px) {
  .admin-grid { grid-template-columns: 1fr; }
  .admin-intro, .audit-heading { flex-direction: column; }
}
</style>
