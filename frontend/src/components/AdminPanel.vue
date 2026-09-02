<template>
  <el-dialog
    :model-value="modelValue"
    title="系统管理"
    width="min(1180px, calc(100vw - 24px))"
    top="3vh"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
    @opened="loadAdminData"
  >
    <div class="admin-intro">
      <div>
        <span class="admin-kicker">仅管理员可见</span>
        <h3>运行状态、完整备份与恢复中心</h3>
        <p>{{ isCloud ? '数据库始终在 ECS 本地数据盘运行；私有 OSS 只接收已校验完整备份。' : '数据库始终在服务器本地运行；共享盘只接收已完成并通过校验的备份包。' }}</p>
      </div>
      <el-button :loading="loading" @click="loadAdminData">刷新全部状态</el-button>
    </div>

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
      show-icon
      class="admin-alert"
    />
    <el-alert
      v-if="restoreState?.pending"
      title="已安排数据恢复，等待服务器重启"
      type="warning"
      :closable="false"
      show-icon
      class="admin-alert"
    >
      <template #default>
        <div class="restore-alert-copy">
          <span>备份 {{ shortId(restoreState.pending.backup_id) }} 已完成全量校验。{{ restartInstruction }}</span>
          <el-button type="warning" link :loading="cancellingRestore" @click="cancelRestore">取消待恢复</el-button>
        </div>
      </template>
    </el-alert>
    <el-alert
      v-else-if="restoreState?.last_result?.state === 'failed'"
      :title="`上次恢复未完成：${restoreState.last_result.error || '请查看服务器日志'}`"
      type="error"
      :closable="false"
      show-icon
      class="admin-alert"
    />
    <el-alert
      v-else-if="restoreState?.last_result?.state === 'completed'"
      :title="`上次恢复已完成，并已自动保留恢复前备份 ${shortId(restoreState.last_result.pre_restore_backup_id)}`"
      type="success"
      :closable="false"
      show-icon
      class="admin-alert"
    />

    <div class="admin-grid">
      <section class="admin-card">
        <div class="card-heading">
          <span class="card-icon health">康</span>
          <div><h4>服务器健康</h4><p>检查数据库、本机磁盘、访问地址和最近使用情况。</p></div>
        </div>
        <template v-if="diagnostics">
          <dl class="status-list">
            <div><dt>数据库</dt><dd :class="diagnostics.database_check === 'ok' ? 'good' : 'bad'">{{ diagnostics.database_check === 'ok' ? '完整性正常' : diagnostics.database_check }}</dd></div>
            <div><dt>本机剩余空间</dt><dd :class="diagnostics.disk_free_percent < 10 ? 'bad' : 'good'">{{ formatBytes(diagnostics.disk_free) }}（{{ diagnostics.disk_free_percent }}%）</dd></div>
            <div><dt>近 10 分钟使用端</dt><dd>{{ diagnostics.recent_users }} 个</dd></div>
            <div><dt>服务器进程身份</dt><dd :title="diagnostics.service_identity">{{ diagnostics.service_identity }}</dd></div>
            <div><dt>访问地址</dt><dd :title="diagnostics.public_url">{{ diagnostics.public_url || '未设置固定地址' }}</dd></div>
          </dl>
          <div class="path-note" :title="diagnostics.data_root">正式数据：{{ diagnostics.data_root }}</div>
        </template>
        <el-skeleton v-else :rows="5" animated />
      </section>

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
            <span></span>{{ aiStatus.configured ? '配置已就绪' : '尚未填写 Key，仍可使用本地规则' }}
          </div>
        </template>
        <el-skeleton v-else :rows="3" animated />
        <el-button type="primary" plain :loading="testingAi" :disabled="!aiStatus" @click="testAi">测试 AI 连接</el-button>
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
          <div><h4>完整业务备份</h4><p>一次备份数据库、导入原件和历史 Word，并生成独立校验清单。</p></div>
        </div>
        <div class="backup-note">
          {{ isCloud ? '本地备份完成并通过 ZIP、SHA256、SQLite 三重校验后，由 ECS 计划任务上传私有 OSS；OSS 暂时不可用不影响业务。' : '本地备份完成并通过 ZIP、SHA256、SQLite 三重校验后，才会尝试复制到共享盘；NAS 断开不影响业务。' }}
        </div>
        <el-button type="primary" plain :loading="backingUp" @click="backupNow">立即创建完整备份</el-button>
        <template v-if="backupResult">
          <div class="backup-result">
            <strong>本地完整备份已完成</strong>
            <span>{{ backupResult.bundle_file }} · {{ formatBytes(backupResult.bundle_size) }} · {{ backupResult.file_count }} 个文件</span>
            <span v-if="backupResult.nas_state === 'synced'">共享盘副本已复制并校验完成</span>
            <span v-else-if="backupResult.nas_error" class="backup-warning">共享盘暂未同步：{{ backupResult.nas_error }}</span>
            <span v-else>{{ isCloud ? '完整备份已安全保留在 ECS 本地；OSS 上传由宿主机计划任务完成。' : '当前未配置共享盘，完整备份已安全保留在服务器本地。' }}</span>
          </div>
        </template>
      </section>

      <section class="admin-card">
        <div class="card-heading">
          <span class="card-icon nas">盘</span>
          <div><h4>{{ isCloud ? 'OSS 异地备份' : '共享盘实际权限' }}</h4><p>{{ isCloud ? '由 ECS RAM 角色和宿主机定时脚本管理，不在应用中保存长期 AccessKey。' : '由正在运行的服务器进程亲自测试，不沿用控制器登录人的权限。' }}</p></div>
        </div>
        <template v-if="diagnostics">
          <dl class="status-list">
            <div><dt>配置状态</dt><dd>{{ isCloud ? '宿主机脚本管理' : diagnostics.nas.configured ? '已配置' : '未配置' }}</dd></div>
            <div><dt>待同步备份</dt><dd :class="diagnostics.backup.pending_nas ? 'bad' : 'good'">{{ diagnostics.backup.pending_nas }} 个</dd></div>
            <div><dt>最近本地备份</dt><dd>{{ diagnostics.backup.latest_local_at ? cnDateTime(diagnostics.backup.latest_local_at) : '尚无' }}</dd></div>
            <div><dt>{{ isCloud ? 'OSS 状态' : '最近 NAS 同步' }}</dt><dd>{{ isCloud ? '请查看宝塔计划任务日志' : diagnostics.backup.latest_nas_at ? cnDateTime(diagnostics.backup.latest_nas_at) : '尚无' }}</dd></div>
          </dl>
        </template>
        <div v-if="!isCloud" class="button-row">
          <el-button plain :loading="testingNas" @click="testNas">以服务身份测试</el-button>
          <el-button plain :loading="syncingPending" :disabled="!diagnostics?.nas.configured" @click="syncPending">重试待同步</el-button>
        </div>
        <el-alert
          v-if="!isCloud && nasTestResult"
          :title="nasTestResult.ok ? `共享盘读写正常（${nasTestResult.latency_ms} ms）` : nasTestResult.message"
          :description="`测试身份：${nasTestResult.identity}`"
          :type="nasTestResult.ok ? 'success' : 'warning'"
          :closable="false"
          show-icon
          class="result-alert"
        />
      </section>
    </div>

    <section v-if="isCloud" class="account-card">
      <div class="audit-heading">
        <div>
          <h4>人员账号与登录状态</h4>
          <p>账号就是人员姓名。初始密码只能首次登录使用；重置密码会立即让该人员所有旧登录失效。</p>
        </div>
        <el-tag effect="plain">{{ accountRows.length }} 个账号</el-tag>
      </div>
      <el-table v-if="accountRows.length" :data="accountRows" max-height="360" size="small" row-key="staff_id">
        <el-table-column prop="name" label="姓名/账号" min-width="120" />
        <el-table-column prop="staff_role" label="岗位" min-width="145" />
        <el-table-column label="权限" width="92" align="center">
          <template #default="{ row }">
            <el-tag :type="row.account_role === 'admin' ? 'warning' : 'info'" size="small">
              {{ row.account_role === 'admin' ? '管理员' : '操作员' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="密码状态" min-width="130" align="center">
          <template #default="{ row }">
            <el-tag :type="accountState(row).type" size="small">{{ accountState(row).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后登录" min-width="165">
          <template #default="{ row }">{{ row.last_login_at ? cnDateTime(row.last_login_at) : '尚未登录' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="warning"
                       :disabled="!row.is_active || row.staff_id === currentStaffId"
                       :loading="busyAccountId === row.staff_id" @click="resetAccount(row)">
              {{ row.staff_id === currentStaffId ? '请用右上角改密' : '重置初始密码' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!loading" description="暂无人员账号" :image-size="64" />
      <el-skeleton v-else :rows="4" animated />
    </section>

    <section class="backup-card">
      <div class="audit-heading">
        <div>
          <h4>备份与恢复中心</h4>
          <p>恢复按钮只安排任务，不会在线替换数据库；下次安全重启时才执行，并自动生成恢复前备份。</p>
        </div>
        <el-tag effect="plain">本地 {{ backupRows.length }} 份</el-tag>
      </div>
      <el-table v-if="backupRows.length" :data="backupRows" max-height="360" size="small" row-key="backup_id">
        <el-table-column label="创建时间" min-width="160">
          <template #default="{ row }">{{ cnDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="105">
          <template #default="{ row }">{{ reasonLabel(row.reason) }}</template>
        </el-table-column>
        <el-table-column label="内容" min-width="150">
          <template #default="{ row }">{{ row.file_count || 0 }} 个文件 · {{ formatBytes(row.bundle_size || 0) }}</template>
        </el-table-column>
        <el-table-column label="本地校验" width="115" align="center">
          <template #default="{ row }">
            <el-tag :type="row.local_present && row.verification === 'verified' ? 'success' : 'danger'" size="small">
              {{ row.local_present && row.verification === 'verified' ? '已验证' : '异常' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="isCloud ? '异地副本' : '共享盘'" min-width="145" align="center">
          <template #default="{ row }">
            <el-tag :type="nasTagType(row.nas_state)" size="small">
              {{ nasStateLabel(row.nas_state) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="260" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :loading="busyBackupId === `${row.backup_id}:verify`" @click="verifyBackup(row)">重新校验</el-button>
            <el-button v-if="diagnostics?.nas.configured && row.nas_state !== 'synced'" link type="warning" :loading="busyBackupId === `${row.backup_id}:sync`" @click="syncBackup(row)">同步 NAS</el-button>
            <el-button link type="danger" :disabled="!!restoreState?.pending" :loading="busyBackupId === `${row.backup_id}:restore`" @click="prepareRestore(row)">安排恢复</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!loading" description="尚无完整备份，建议现在创建第一份" :image-size="64" />
      <el-skeleton v-else :rows="4" animated />
    </section>

    <section class="audit-card">
      <div class="audit-heading">
        <div><h4>最近操作记录</h4><p>只记录操作人、接口、结果和时间，不记录表单内容、口令或 API Key。</p></div>
        <el-tag effect="plain">最近 {{ auditRows.length }} 条</el-tag>
      </div>
      <el-table v-if="auditRows.length" :data="auditRows" max-height="300" size="small" row-key="id">
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
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  api, cnDateTime, type AiAdminStatus, type AiConnectionResult,
  type AccountView, type AuditEventView, type BackupItem, type BackupResult,
  type DiagnosticsView, type NasTestView, type RestoreStateView
} from '@/api'

defineProps<{ modelValue: boolean; currentStaffId?: number }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const loading = ref(false)
const testingAi = ref(false)
const testingNas = ref(false)
const syncingPending = ref(false)
const backingUp = ref(false)
const cancellingRestore = ref(false)
const busyBackupId = ref('')
const busyAccountId = ref<number | null>(null)
const loadError = ref('')
const aiStatus = ref<AiAdminStatus | null>(null)
const aiTestResult = ref<AiConnectionResult | null>(null)
const nasTestResult = ref<NasTestView | null>(null)
const diagnostics = ref<DiagnosticsView | null>(null)
const restoreState = ref<RestoreStateView | null>(null)
const auditRows = ref<AuditEventView[]>([])
const accountRows = ref<AccountView[]>([])
const backupRows = ref<BackupItem[]>([])
const backupResult = ref<BackupResult | null>(null)
const isCloud = computed(() => diagnostics.value?.mode === 'cloud')
const restartInstruction = computed(() => restoreState.value?.pending?.instruction
  || (isCloud.value
    ? '请联系管理员重启云端应用；系统会先备份当前数据，再执行恢复。'
    : '请到服务器控制器点击“重启服务器”；系统会先备份当前数据，再执行恢复。'))

async function loadAdminData() {
  loading.value = true
  loadError.value = ''
  try {
    const [status, audit, backups, health, restore, accounts] = await Promise.all([
      api.adminAiStatus(), api.adminAudit(30), api.adminBackups(),
      api.adminDiagnostics(), api.adminRestoreState(), api.adminAccounts()
    ])
    aiStatus.value = status
    auditRows.value = audit
    backupRows.value = backups
    diagnostics.value = health
    restoreState.value = restore
    accountRows.value = accounts
  } catch (error: any) {
    if (error?.response?.status === 403) loadError.value = '当前身份没有管理员权限。'
    else loadError.value = error?.response?.data?.detail || '管理信息加载失败，请检查服务器连接。'
  } finally {
    loading.value = false
  }
}

async function refreshSafetyData() {
  const [backups, health, restore] = await Promise.all([
    api.adminBackups(), api.adminDiagnostics(), api.adminRestoreState()
  ])
  backupRows.value = backups
  diagnostics.value = health
  restoreState.value = restore
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
    if (backupResult.value.nas_state === 'pending') ElMessage.warning('本地完整备份成功；共享盘暂不可用，已加入待同步队列。')
    else ElMessage.success(isCloud.value
      ? '数据库、导入原件和历史 Word 已完成本地备份；OSS 将由计划任务同步'
      : '数据库、导入原件和历史 Word 已完成备份与校验')
    await refreshSafetyData()
    await loadAuditOnly()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '完整备份失败')
  } finally {
    backingUp.value = false
  }
}

async function testNas() {
  testingNas.value = true
  nasTestResult.value = null
  try {
    nasTestResult.value = await api.adminTestNas()
    if (nasTestResult.value.ok) ElMessage.success('共享盘权限测试通过')
    else ElMessage.warning(nasTestResult.value.message)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '共享盘权限测试失败')
  } finally {
    testingNas.value = false
  }
}

async function syncPending() {
  syncingPending.value = true
  try {
    const result = await api.adminSyncPending()
    await refreshSafetyData()
    if (result.failed) ElMessage.warning(`已同步 ${result.synced} 份，仍有 ${result.failed} 份失败。`)
    else if (result.attempted) ElMessage.success(`已补同步 ${result.synced} 份完整备份。`)
    else ElMessage.info('当前没有待同步备份。')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '待同步备份重试失败')
  } finally {
    syncingPending.value = false
  }
}

async function verifyBackup(row: BackupItem) {
  busyBackupId.value = `${row.backup_id}:verify`
  try {
    await api.adminVerifyBackup(row.backup_id)
    await refreshSafetyData()
    ElMessage.success('备份 ZIP、全部文件 SHA256 和 SQLite 完整性均正常')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '备份校验失败')
  } finally {
    busyBackupId.value = ''
  }
}

async function syncBackup(row: BackupItem) {
  busyBackupId.value = `${row.backup_id}:sync`
  try {
    await api.adminSyncBackup(row.backup_id)
    await refreshSafetyData()
    ElMessage.success('共享盘副本已复制并校验完成')
  } catch (error: any) {
    ElMessage.warning(error?.response?.data?.detail || error?.message || '共享盘同步失败')
    await refreshSafetyData()
  } finally {
    busyBackupId.value = ''
  }
}

async function prepareRestore(row: BackupItem) {
  try {
    await ElMessageBox.confirm(
      `将安排恢复到 ${cnDateTime(row.created_at)} 的数据。系统现在只做全量校验并登记任务；下次重启前还会自动备份当前全部数据。是否继续？`,
      '安排安全恢复',
      { type: 'warning', confirmButtonText: '校验并安排恢复', cancelButtonText: '取消' }
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  busyBackupId.value = `${row.backup_id}:restore`
  try {
    const request = await api.adminPrepareRestore(row.backup_id)
    await refreshSafetyData()
    ElMessage.warning(request.instruction || (isCloud.value
      ? '恢复任务已安排。请联系管理员重启云端应用。'
      : '恢复任务已安排。请到服务器控制器点击“重启服务器”。'))
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '无法安排恢复')
  } finally {
    busyBackupId.value = ''
  }
}

async function cancelRestore() {
  cancellingRestore.value = true
  try {
    await api.adminCancelRestore()
    await refreshSafetyData()
    ElMessage.success('待恢复任务已取消，当前数据没有变化。')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '取消恢复失败')
  } finally {
    cancellingRestore.value = false
  }
}

async function loadAuditOnly() {
  try { auditRows.value = await api.adminAudit(30) } catch { /* 主操作结果已经显示 */ }
}

async function resetAccount(row: AccountView) {
  try {
    await ElMessageBox.confirm(
      `确认把“${row.name}”重置为系统初始密码吗？该人员所有已登录设备会立即退出，下一次登录必须设置新密码。`,
      '重置人员密码',
      { type: 'warning', confirmButtonText: '确认重置', cancelButtonText: '取消' }
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  busyAccountId.value = row.staff_id
  try {
    await api.adminResetPassword(row.staff_id)
    accountRows.value = await api.adminAccounts()
    await loadAuditOnly()
    ElMessage.success(`${row.name} 已重置；请单独告知其使用初始密码登录并立即修改。`)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : detail?.message || '账号密码重置失败')
  } finally {
    busyAccountId.value = null
  }
}

function accountState(row: AccountView): { label: string; type: 'success' | 'warning' | 'info' | 'danger' } {
  if (!row.is_active) return { label: '已停用', type: 'info' }
  if (!row.password_initialized) return { label: '未初始化', type: 'danger' }
  if (row.must_change_password) return { label: '待首次改密', type: 'warning' }
  return { label: '个人密码已设置', type: 'success' }
}

function formatBytes(size: number) {
  if (!Number.isFinite(size)) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function shortId(value?: string) {
  if (!value) return '—'
  return value.length > 20 ? `${value.slice(0, 17)}…` : value
}

function reasonLabel(reason: string) {
  const labels: Record<string, string> = {
    manual: '手动备份', daily: '每日自动', 'pre-restore': '恢复前留底', test: '测试'
  }
  return labels[reason] || reason || '其他'
}

function nasStateLabel(state: string) {
  if (isCloud.value && state === 'not_configured') return '宿主机管理'
  return state === 'synced' ? '已校验同步'
    : state === 'pending' ? '等待重试'
      : state === 'not_configured' ? '未配置' : '未知'
}

function nasTagType(state: string): 'success' | 'warning' | 'info' | 'danger' {
  return state === 'synced' ? 'success' : state === 'pending' ? 'warning' : 'info'
}

function auditAction(method: string, path: string) {
  const operation: Record<string, string> = { POST: '新增/执行', PATCH: '修改', DELETE: '删除', PUT: '更新' }
  const area = path.includes('/restore') ? '恢复任务'
    : path.includes('/backups') || path.includes('/backup') ? (isCloud.value ? '备份与 OSS' : '备份与共享盘')
      : path.includes('/admin/accounts') ? '人员账号'
        : path.includes('/session/change-password') ? '个人密码'
      : path.includes('/render') ? '生成 Word'
        : path.includes('/imports/') ? '导入数据'
          : path.includes('/handover-items') || path.includes('/items') ? '交接事项'
            : path.includes('/external-assessments') ? '外委考核'
              : path.includes('/device-changes') ? '设备变更'
                : path.includes('/general-items') ? '定期工作'
                  : path.includes('/handover-station-meta') ? '基本信息'
                    : path.includes('/handovers') ? '班次' : '系统'
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
.restore-alert-copy { display: flex; align-items: center; justify-content: space-between; gap: 16px; width: 100%; }
.admin-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.admin-card, .account-card, .audit-card, .backup-card { padding: 18px; border: 1px solid #e2eaf3; border-radius: 14px; background: #fbfdff; }
.card-heading { margin-bottom: 15px; display: flex; gap: 11px; }
.card-heading h4 { margin: 1px 0 4px; color: #27415d; }
.card-icon { width: 38px; height: 38px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 11px; font-size: 12px; font-weight: 850; }
.card-icon.health { color: #17618c; background: #e3f3fb; }
.card-icon.ai { color: #5b3fad; background: #efeafd; }
.card-icon.backup { color: #176e50; background: #e5f7ef; }
.card-icon.nas { color: #9a5c0a; background: #fff0d5; }
.status-list { margin: 0 0 12px; display: grid; gap: 7px; }
.status-list div { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
.status-list dt { color: #7c8b9b; }
.status-list dd { margin: 0; max-width: 70%; overflow: hidden; color: #304962; text-overflow: ellipsis; white-space: nowrap; }
.status-list dd.good { color: #19744f; font-weight: 700; }
.status-list dd.bad { color: #b15527; font-weight: 700; }
.status-line { margin-bottom: 13px; display: flex; align-items: center; gap: 8px; color: #52687d; font-size: 12px; }
.status-line span { width: 8px; height: 8px; border-radius: 50%; }
.status-line.ready span { background: #31ae78; box-shadow: 0 0 0 4px #dff5ec; }
.status-line.warning span { background: #dfa139; box-shadow: 0 0 0 4px #fff0d2; }
.path-note { padding: 8px 10px; overflow: hidden; color: #6b7f92; border-radius: 8px; background: #f0f5f9; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.result-alert { margin-top: 12px; }
.backup-note { min-height: 58px; margin-bottom: 13px; padding: 12px; color: #5e7186; border-radius: 9px; background: #eef5fb; font-size: 12px; line-height: 1.65; }
.backup-result { margin-top: 12px; display: grid; gap: 4px; color: #5d7085; font-size: 11px; line-height: 1.5; }
.backup-result strong { color: #217052; font-size: 12px; }
.backup-warning { color: #a8680b; }
.button-row { display: flex; flex-wrap: wrap; gap: 8px; }
.account-card, .backup-card, .audit-card { margin-top: 14px; background: #fff; }
.audit-heading { margin-bottom: 12px; }
@media (max-width: 760px) {
  .admin-grid { grid-template-columns: 1fr; }
  .admin-intro, .audit-heading, .restore-alert-copy { flex-direction: column; }
}
</style>
