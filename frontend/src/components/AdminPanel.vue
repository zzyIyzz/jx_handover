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
      v-if="diagnostics && diagnostics.admin_configured === false"
      title="当前没有任何管理员账号"
      type="error"
      :closable="false"
      show-icon
      class="admin-alert"
    >
      <template #default>
        <div class="restore-alert-copy">
          <span>人员账号将全部变成操作员，备份与恢复入口也会消失。请在服务器执行
            python backend/scripts/manage_admin.py --grant 姓名，或在 .env 填写 JX_ADMIN_NAMES 后重启。</span>
        </div>
      </template>
    </el-alert>
    <el-alert
      v-for="(warning, index) in dataRootWarnings"
      :key="`data-root-${index}`"
      :title="warning"
      type="warning"
      :closable="false"
      show-icon
      class="admin-alert"
    />
    <el-alert
      v-if="restoreState?.pending"
      :title="restoreState.pending.state === 'invalid' ? '待恢复任务记录异常，请联系管理员检查' : '已安排数据恢复，尚未执行；等待应用服务安全重启'"
      type="warning"
      :closable="false"
      show-icon
      class="admin-alert"
    >
      <template #default>
        <div class="restore-alert-copy">
          <span v-if="restoreState.pending.state === 'invalid'">{{ restoreState.pending.error }}。不要直接重启，请先核实任务或取消待恢复。</span>
          <span v-else>目标备份 {{ restoreState.pending.backup_id }} · 申请人 {{ restoreState.pending.requested_by || '未知' }} · {{ cnDateTime(restoreState.pending.requested_at) }}。安排后继续录入的数据将被目标备份替换，请通知用户暂停操作。{{ restartInstruction }}</span>
          <el-button type="warning" link :loading="cancellingRestore" @click="cancelRestore">取消待恢复</el-button>
        </div>
      </template>
    </el-alert>
    <el-alert
      v-if="restoreState?.last_result && ['failed', 'invalid', 'rollback_failed'].includes(restoreState.last_result.state)"
      :title="`上次恢复未完成：${restoreState.last_result.error || '请查看服务器日志'}`"
      type="error"
      :closable="false"
      show-icon
      class="admin-alert"
    />
    <el-alert
      v-else-if="restoreState?.last_result?.state === 'completed'"
      title="上次恢复已完成"
      :description="`目标备份 ${restoreState.last_result.backup_id || '未知'} · ${cnDateTime(restoreState.last_result.completed_at)}。${restoreState.last_result.pre_restore_backup_id ? `恢复前备份：${restoreState.last_result.pre_restore_backup_id}` : '本次没有恢复前备份（例如空服务器首次恢复）'}。请刷新业务页面并核对数据；如需重新登录，请使用备份中的账号。`"
      type="success"
      :closable="false"
      show-icon
      class="admin-alert"
    />

    <div v-if="diagnostics" class="overview-grid">
      <section class="admin-card">
        <h4>当前服务版本</h4>
        <strong class="overview-value">V{{ diagnostics.version }}</strong>
        <p>{{ { cloud: '云服务器', server: '局域网服务器', desktop: '本机' }[diagnostics.mode] }} · {{ diagnostics.account_login_enabled ? '个人账号登录' : '共享身份模式' }}</p>
        <small>状态检查：{{ cnDateTime(diagnostics.checked_at) }}</small>
      </section>
      <section class="admin-card">
        <h4>最近完整备份</h4>
        <strong class="overview-value">{{ diagnostics.backup.latest_local_at ? cnDateTime(diagnostics.backup.latest_local_at) : '尚未备份' }}</strong>
        <p>{{ autoBackupLabel }}</p>
        <small>本地保留 {{ diagnostics.backup.total }} 份 · 不代表已上传 OSS</small>
      </section>
      <section class="admin-card">
        <h4>OSS 同步结果</h4>
        <el-tag :type="ossTagType">{{ ossLabel }}</el-tag>
        <p>{{ diagnostics.oss.message }}</p>
        <small>最近成功：{{ diagnostics.oss.last_success_at ? cnDateTime(diagnostics.oss.last_success_at) : '尚无记录' }}</small>
      </section>
      <section class="admin-card">
        <h4>管理员与数据目录</h4>
        <strong class="overview-value">{{ adminSummary }}</strong>
        <p :title="diagnostics.data_root">数据：{{ diagnostics.data_root }}</p>
        <small>{{ dataRootFootnote }}</small>
      </section>
    </div>

    <div class="admin-grid">
      <section class="admin-card">
        <div class="card-heading">
          <span class="card-icon health">康</span>
          <div><h4>服务器健康</h4><p>检查数据库、本机磁盘、访问地址和最近使用情况。</p></div>
        </div>
        <template v-if="diagnostics">
          <dl class="status-list">
            <div><dt>数据库</dt><dd :class="diagnostics.database_check === 'ok' ? 'good' : 'bad'">{{ diagnostics.database_check === 'ok' ? '完整性正常' : diagnostics.database_check }}</dd></div>
            <div><dt>数据库大小</dt><dd>{{ diagnostics.database_size ? formatBytes(diagnostics.database_size) : '尚未建立' }}</dd></div>
            <div><dt>本机剩余空间</dt><dd :class="diagnostics.disk_free_percent < 10 ? 'bad' : 'good'">{{ formatBytes(diagnostics.disk_free) }}（{{ diagnostics.disk_free_percent }}%）</dd></div>
            <div><dt>管理员</dt><dd :class="diagnostics.admin_configured === false ? 'bad' : 'good'">{{ adminSummary }}</dd></div>
            <div><dt>近 10 分钟使用端</dt><dd>{{ diagnostics.recent_users }} 个</dd></div>
            <div><dt>服务器进程身份</dt><dd :title="diagnostics.service_identity">{{ diagnostics.service_identity }}</dd></div>
            <div><dt>访问地址</dt><dd :title="diagnostics.public_url">{{ diagnostics.public_url || '未设置固定地址' }}</dd></div>
          </dl>
          <div class="data-root-box">
            <div class="data-root-title">
              <span>账号信息与交接班记录的存放位置</span>
              <el-tag size="small" :type="diagnostics.data_root_report?.data_root_explicit ? 'success' : 'info'" effect="plain">
                {{ diagnostics.data_root_report?.data_root_explicit ? '已显式固定' : '程序默认目录' }}
              </el-tag>
            </div>
            <div class="path-note" :title="diagnostics.database_path">数据库：{{ diagnostics.database_path }}</div>
            <div class="path-note" :title="diagnostics.data_root">数据根：{{ diagnostics.data_root }}</div>
            <div v-if="diagnostics.data_root_report?.adopted_from" class="path-note">
              已从旧目录接管：{{ diagnostics.data_root_report.adopted_from }}
            </div>
            <div v-for="legacy in diagnostics.data_root_report?.legacy_roots_with_data || []" :key="legacy"
                 class="path-note legacy" :title="legacy">
              旧目录仍有数据（未使用）：{{ legacy }}
            </div>
            <div class="data-root-hint">
              更换服务器或运行方式前，先在此确认目录；也可在服务器执行
              python backend/scripts/data_location.py 查看和接管。
            </div>
          </div>
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
            <div><dt>运行模式</dt><dd>{{ aiStatus.mode === 'qwen' ? 'Qwen 智能整理' : '本地确定性规则' }}</dd></div>
            <div v-if="aiStatus.mode_requested && aiStatus.mode_requested !== aiStatus.mode">
              <dt>配置要求</dt><dd>AI_MODE={{ aiStatus.mode_requested }}（未生效）</dd>
            </div>
            <div><dt>模型</dt><dd>{{ aiStatus.mode === 'qwen'
              ? (aiStatus.model || '—')
              : `未启用（已配置 ${aiStatus.configured_model || 'Qwen 模型'}）` }}</dd></div>
            <div><dt>API Key</dt><dd>{{ aiStatus.key_hint ? `已填写 ${aiStatus.key_hint}` : '未填写' }}</dd></div>
          </dl>
          <div class="status-line" :class="aiStatus.mode === 'qwen' ? 'ready' : 'warning'">
            <span></span>{{ aiStatus.mode === 'qwen'
              ? '导入工作日志时会调用 AI 智能整理，失败自动回退到本地规则。'
              : (aiStatus.unavailable_reason || '未启用云端 AI，导入仍可使用本地规则。') }}
          </div>
          <div v-if="aiStatus.mode !== 'qwen'" class="ai-guide">
            <strong>开启 AI 智能整理</strong>
            <ol>
              <li>在阿里云百炼控制台创建 API Key，并开通 {{ aiStatus.configured_model || 'Qwen 模型' }}。</li>
              <li>Windows 服务端：打开服务端控制器 → 填写 API Key → 保存并重启服务器。</li>
              <li>云端/源码部署：在服务器 .env 设 AI_MODE=auto、填写 QWEN_API_KEY，然后重启服务并重新构建前端。</li>
              <li>重启后回到本页点“测试 AI 连接”，确认识别为 Qwen。</li>
            </ol>
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
            <div v-if="!isCloud"><dt>待同步备份</dt><dd :class="diagnostics.backup.pending_nas ? 'bad' : 'good'">{{ diagnostics.backup.pending_nas }} 个</dd></div>
            <div v-if="isCloud"><dt>本次同步数量</dt><dd>{{ diagnostics.oss.synced_count }} 组</dd></div>
            <div v-if="isCloud"><dt>最近任务回执</dt><dd>{{ diagnostics.oss.updated_at ? cnDateTime(diagnostics.oss.updated_at) : '尚无记录' }}</dd></div>
            <div v-if="isCloud"><dt>最近成功备份时间</dt><dd>{{ diagnostics.oss.latest_backup_at ? cnDateTime(diagnostics.oss.latest_backup_at) : '尚无记录' }}</dd></div>
            <div><dt>最近本地备份</dt><dd>{{ diagnostics.backup.latest_local_at ? cnDateTime(diagnostics.backup.latest_local_at) : '尚无' }}</dd></div>
            <div><dt>自动备份</dt><dd :class="diagnostics.backup.auto_backup?.last_error ? 'bad' : 'good'">{{ autoBackupLabel }}</dd></div>
            <div><dt>{{ isCloud ? 'OSS 状态' : '最近 NAS 同步' }}</dt><dd>{{ isCloud ? ossLabel : diagnostics.backup.latest_nas_at ? cnDateTime(diagnostics.backup.latest_nas_at) : '尚无' }}</dd></div>
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

    <section v-if="accountLoginEnabled" class="account-card">
      <div class="audit-heading">
        <div>
          <h4>人员账号与登录状态</h4>
          <p>账号就是人员姓名。可在此添加人员、改名、设为/取消管理员或停用启用；初始密码只能首次登录使用，重置密码会立即让该人员所有旧登录失效。</p>
        </div>
        <el-tag effect="plain">{{ accountRows.length }} 个账号</el-tag>
      </div>
      <div class="account-toolbar">
        <el-input v-model="newStaffName" size="small" placeholder="新人员姓名（即登录账号）" style="width: 220px" />
        <el-button size="small" type="primary" :loading="addingStaff" @click="addStaff">添加人员</el-button>
        <span class="toolbar-hint">只有管理员能看到本页；停用后该人员无法登录，可随时再启用；改名或取消管理员会立即让其旧登录失效。</span>
      </div>
      <el-table v-if="accountRows.length" :data="accountRows" max-height="360" size="small" row-key="staff_id">
        <el-table-column prop="name" label="姓名/账号" min-width="120" />
        <el-table-column label="权限" width="126" align="center">
          <template #default="{ row }">
            <el-tag :type="row.account_role === 'admin' ? 'warning' : 'info'" size="small">
              {{ row.account_role === 'admin' ? '管理员' : '操作员' }}
            </el-tag>
            <div v-if="row.admin_from_env" class="role-hint" title="该姓名写在服务器环境变量 JX_ADMIN_NAMES 中，每次启动都会被提升为管理员">
              由服务器配置指定
            </div>
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
        <el-table-column label="操作" width="300" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link :type="row.account_role === 'admin' ? 'danger' : 'primary'"
                       :disabled="!row.is_active || row.staff_id === currentStaffId || !!row.admin_from_env"
                       :loading="busyAdminId === row.staff_id" @click="toggleAdmin(row)">
              {{ row.account_role === 'admin' ? '取消管理员' : '设为管理员' }}
            </el-button>
            <el-button link type="primary" :disabled="row.staff_id === currentStaffId" @click="renameStaff(row)">
              改名
            </el-button>
            <el-button link :type="row.is_active ? 'danger' : 'success'"
                       :disabled="row.staff_id === currentStaffId" @click="toggleStaff(row)">
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
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
const busyAdminId = ref<number | null>(null)
const newStaffName = ref('')
const addingStaff = ref(false)
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
const accountLoginEnabled = computed(() => diagnostics.value?.account_login_enabled)
const ossLabel = computed(() => {
  const oss = diagnostics.value?.oss
  if (oss?.stale) return '状态已过期'
  return ({ success: '同步成功', running: '正在同步', failed: '同步失败', invalid: '记录异常', unknown: '尚无记录' } as Record<string, string>)[oss?.state || 'unknown'] || '尚无记录'
})
const ossTagType = computed(() => diagnostics.value?.oss.stale ? 'warning'
  : diagnostics.value?.oss.state === 'success' ? 'success'
  : diagnostics.value?.oss.state === 'failed' || diagnostics.value?.oss.state === 'invalid' ? 'danger' : 'info')
const restartInstruction = computed(() => restoreState.value?.restart_instruction
  || restoreState.value?.pending?.instruction
  || '请联系管理员按实际部署方式安全重启应用服务；不需要重启整台服务器。')
const adminSummary = computed(() => {
  const names = diagnostics.value?.administrators || []
  if (!names.length) return '无（需要恢复）'
  return names.length <= 3 ? names.join('、') : `${names.length} 人（${names.slice(0, 3).join('、')} 等）`
})
const dataRootWarnings = computed(() => diagnostics.value?.data_root_report?.warnings || [])
const dataRootFootnote = computed(() => {
  const report = diagnostics.value?.data_root_report
  if (!report) return '数据目录状态未返回'
  return report.has_database
    ? `数据库 ${formatBytes(report.database_size)} · ${report.data_root_explicit ? '已显式固定目录' : '使用程序默认目录'}`
    : '该目录下还没有数据库'
})

async function loadAdminData() {
  loading.value = true
  loadError.value = ''
  try {
    const results = await Promise.allSettled([
      api.adminAiStatus(), api.adminAudit(30), api.adminBackups(),
      api.adminDiagnostics(), api.adminRestoreState(), api.adminAccounts()
    ] as const)
    const [status, audit, backups, health, restore, accounts] = results
    if (status.status === 'fulfilled') aiStatus.value = status.value
    if (audit.status === 'fulfilled') auditRows.value = audit.value
    if (backups.status === 'fulfilled') backupRows.value = backups.value
    if (health.status === 'fulfilled') diagnostics.value = health.value
    if (restore.status === 'fulfilled') restoreState.value = restore.value
    if (accounts.status === 'fulfilled') accountRows.value = accounts.value
    const labels = ['AI 配置', '操作记录', '备份列表', '运行状态', '恢复状态', '人员账号']
    const failed = results.flatMap((result, index) => result.status === 'rejected' ? [labels[index]] : [])
    if (failed.length) loadError.value = `${failed.join('、')}加载失败，其余信息已显示；请检查连接或权限后刷新。`
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
      `将安排恢复到 ${cnDateTime(row.created_at)} 的备份（${row.backup_id}）。数据库、上传附件和生成文件将被该备份替换，账号及密码也将回到备份时状态；之后录入的数据不会合并。系统现在只校验并登记任务，下次应用启动前会备份当前数据并执行恢复。请通知用户暂停操作并安排维护时间。是否继续？`,
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
    ElMessage.warning(`恢复任务已安排，尚未执行。${request.instruction || restartInstruction.value}`)
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

async function addStaff() {
  const name = newStaffName.value.trim()
  if (!name) {
    ElMessage.warning('请先输入新人员姓名。')
    return
  }
  addingStaff.value = true
  try {
    await api.staffAdd(name)
    newStaffName.value = ''
    accountRows.value = await api.adminAccounts()
    await loadAuditOnly()
    ElMessage.success(`已添加账号 ${name}；请告知其使用初始密码登录并立即修改。`)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : detail?.message || '添加人员失败')
  } finally {
    addingStaff.value = false
  }
}

async function toggleAdmin(row: AccountView) {
  const granting = row.account_role !== 'admin'
  try {
    await ElMessageBox.confirm(
      granting
        ? `确认把“${row.name}”设为管理员？其将能看到本管理页，并可创建备份、安排恢复、重置他人密码。`
        : `确认取消“${row.name}”的管理员权限？其已登录设备会立即退出，需要重新登录。`,
      granting ? '设为管理员' : '取消管理员',
      { type: 'warning', confirmButtonText: granting ? '确认设为管理员' : '确认取消', cancelButtonText: '取消' }
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  busyAdminId.value = row.staff_id
  try {
    await api.adminPatchAccount(row.staff_id, { is_admin: granting })
    accountRows.value = await api.adminAccounts()
    diagnostics.value = await api.adminDiagnostics()
    await loadAuditOnly()
    ElMessage.success(granting
      ? `${row.name} 已获得管理员权限，其需重新登录后才能看到系统管理。`
      : `${row.name} 的管理员权限已取消。`)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : detail?.message || '管理员权限调整失败')
  } finally {
    busyAdminId.value = null
  }
}

async function renameStaff(row: AccountView) {
  let name = ''
  try {
    const result = await ElMessageBox.prompt(
      `请输入“${row.name}”的新姓名；改名后其所有已登录设备会立即退出。`,
      '人员改名',
      {
        confirmButtonText: '确认改名',
        cancelButtonText: '取消',
        inputValue: row.name,
        inputValidator: (value: string) => (value && value.trim() ? true : '姓名不能为空'),
      }
    )
    name = String(result.value).trim()
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  try {
    await api.adminPatchAccount(row.staff_id, { name })
    accountRows.value = await api.adminAccounts()
    await loadAuditOnly()
    ElMessage.success(`${row.name} 已改名为 ${name}。`)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : detail?.message || '改名失败')
  }
}

async function toggleStaff(row: AccountView) {
  const disabling = row.is_active
  try {
    await ElMessageBox.confirm(
      disabling
        ? `确认停用“${row.name}”？其将无法登录，已登录设备会立即退出；后续可随时再启用。`
        : `确认重新启用“${row.name}”？启用后其可再次登录系统。`,
      disabling ? '停用人员' : '启用人员',
      { type: 'warning', confirmButtonText: disabling ? '确认停用' : '确认启用', cancelButtonText: '取消' }
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  try {
    await api.adminPatchAccount(row.staff_id, { is_active: !disabling })
    accountRows.value = await api.adminAccounts()
    await loadAuditOnly()
    ElMessage.success(`${row.name} 已${disabling ? '停用' : '启用'}。`)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : detail?.message || '状态修改失败')
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

const autoBackupLabel = computed(() => {
  const auto = diagnostics.value?.backup?.auto_backup
  if (!auto) return '——'
  if (auto.last_error) return `上次自动备份异常：${auto.last_error}`
  const intervalMin = Math.max(1, Math.round(auto.check_interval_seconds / 60))
  if (!auto.scheduler_started) return '自动备份未启动，请检查服务运行方式'
  return `每天自动备份（每 ${intervalMin} 分钟检查）；保留每日备份 ${auto.keep_daily} 份、其他备份 ${auto.keep_manual} 份`
})

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
.overview-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 18px 0; }
.overview-grid h4 { margin: 0 0 12px; }
.overview-grid p { color: #526579; line-height: 1.7; }
.overview-grid small { color: #6b7c8f; }
.overview-value { display: block; font-size: 20px; color: #173856; overflow-wrap: anywhere; }
@media (max-width: 1080px) { .overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .overview-grid { grid-template-columns: 1fr; } }
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
.data-root-box { display: grid; gap: 6px; margin-top: 10px; }
.data-root-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #46596d; font-size: 12px; font-weight: 700; }
.data-root-hint { color: #7b8ca0; font-size: 11px; line-height: 1.6; }
.path-note.legacy { color: #a8680b; background: #fdf4e6; }
.role-hint { margin-top: 3px; color: #8b98a8; font-size: 10px; }
.ai-guide { margin: 12px 0; padding: 11px 12px; border-radius: 9px; background: #eef5fb; }
.ai-guide strong { display: block; margin-bottom: 6px; color: #24527f; font-size: 12px; }
.ai-guide ol { margin: 0; padding-left: 18px; color: #55697e; font-size: 11px; line-height: 1.85; }
.result-alert { margin-top: 12px; }
.backup-note { min-height: 58px; margin-bottom: 13px; padding: 12px; color: #5e7186; border-radius: 9px; background: #eef5fb; font-size: 12px; line-height: 1.65; }
.backup-result { margin-top: 12px; display: grid; gap: 4px; color: #5d7085; font-size: 11px; line-height: 1.5; }
.backup-result strong { color: #217052; font-size: 12px; }
.backup-warning { color: #a8680b; }
.button-row { display: flex; flex-wrap: wrap; gap: 8px; }
.account-card, .backup-card, .audit-card { margin-top: 14px; background: #fff; }
.audit-heading { margin-bottom: 12px; }
.account-toolbar { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; }
.toolbar-hint { color: #748599; font-size: 12px; }
@media (max-width: 760px) {
  .admin-grid { grid-template-columns: 1fr; }
  .admin-intro, .audit-heading, .restore-alert-copy { flex-direction: column; }
}
</style>
