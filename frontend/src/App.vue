<template>
  <div v-if="sessionLoading" class="session-loading" v-loading="true">
    <span>正在连接交接班服务器…</span>
  </div>

  <main v-else-if="serverUnavailable" class="login-page">
    <section class="login-card connection-card">
      <div class="login-mark connection-mark">!</div>
      <span class="login-eyebrow">暂时无法连接</span>
      <h1>{{ networkOnline ? '系统初始化失败' : '交接班服务器未响应' }}</h1>
      <p>{{ connectionError || '请确认这台电脑已连接检修中心局域网，并联系管理员检查服务器是否正在运行。恢复连接后可直接重试，不需要关闭浏览器。' }}</p>
      <el-button type="primary" size="large" class="login-button" :loading="retrying" @click="retryConnection">
        重新连接
      </el-button>
      <div class="login-note">当前页面没有提交任何修改；已保存的数据仍保留在服务器。</div>
    </section>
  </main>

  <main v-else-if="needsLogin" class="login-page">
    <section class="login-card">
      <div class="login-mark">交</div>
      <span class="login-eyebrow">江西片区检修中心</span>
      <h1>进入智能交接班系统</h1>
      <p>选择自己的姓名即可进入；系统会记录操作人，人员列表中只显示姓名。</p>
      <el-form label-position="top" @submit.prevent="login">
        <el-form-item label="我的姓名" required>
          <el-select v-model="loginForm.name" filterable placeholder="请选择或搜索姓名" class="login-control">
            <el-option v-for="name in sessionOptions?.staff_names || []" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="sessionOptions?.access_code_required" label="系统访问口令" required>
          <el-input v-model="loginForm.accessCode" type="password" show-password autocomplete="current-password"
                    placeholder="请输入管理员提供的访问口令" class="login-control" />
        </el-form-item>
        <el-button type="primary" size="large" class="login-button" :loading="loginLoading" @click="login">
          进入系统
        </el-button>
      </el-form>
      <div class="login-note">Qwen API Key 只保存在服务器端，使用人员无需填写。</div>
    </section>
  </main>

  <el-container v-else class="layout">
    <transition name="connection-slide">
      <div v-if="!networkOnline" class="network-banner" role="status">
        <strong>服务器连接已中断</strong>
        <span>请先不要重复提交；系统恢复后会自动刷新最新数据。</span>
        <el-button size="small" plain :loading="retrying" @click="retryConnection">立即重试</el-button>
      </div>
    </transition>
    <el-header class="app-header">
      <div class="header-inner">
        <router-link to="/" class="brand" aria-label="返回交接班工作台">
          <span class="brand-mark">交</span>
          <span class="brand-copy">
            <strong>江西片区智能交接班</strong>
            <small>让每次交接更清楚、更省事</small>
          </span>
        </router-link>
        <div class="header-meta">
          <span class="safe-dot" :class="{ offline: !networkOnline }"></span>
          <span class="safe-text">{{ !networkOnline ? '服务器连接中断' : sessionOptions?.mode === 'server' ? '数据统一保存在服务器' : '数据本地保存' }}</span>
          <el-dropdown v-if="session?.name" trigger="click" @command="handleUserCommand">
            <button class="user-chip" type="button">{{ session.name }}⌄</button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="session.role === 'admin'" command="admin">系统管理</el-dropdown-item>
                <el-dropdown-item command="logout" :divided="session.role === 'admin'">退出当前身份</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <span class="version">V0.4.1 生产加固测试版</span>
        </div>
      </div>
    </el-header>
    <el-main class="app-main">
      <router-view />
    </el-main>
    <admin-panel v-if="session?.role === 'admin'" v-model="adminPanel" />
  </el-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type SessionOptions, type SessionState } from '@/api'
import AdminPanel from '@/components/AdminPanel.vue'

const sessionLoading = ref(true)
const loginLoading = ref(false)
const retrying = ref(false)
const networkOnline = ref(navigator.onLine)
const adminPanel = ref(false)
const connectionError = ref('')
const sessionOptions = ref<SessionOptions | null>(null)
const session = ref<SessionState | null>(null)
const loginForm = reactive({ name: '', accessCode: '' })
const needsLogin = computed(() => Boolean(sessionOptions.value?.auth_required && !session.value?.authenticated))
const serverUnavailable = computed(() => !sessionOptions.value && (!networkOnline.value || Boolean(connectionError.value)))

async function loadSession() {
  sessionLoading.value = true
  connectionError.value = ''
  try {
    const [options, current] = await Promise.all([api.sessionOptions(), api.sessionMe()])
    sessionOptions.value = options
    session.value = current
    networkOnline.value = true
  } catch (error: any) {
    networkOnline.value = Boolean(error?.response)
    connectionError.value = error?.response?.data?.detail
      || (networkOnline.value ? '服务器已响应，但系统初始化失败。请联系管理员查看服务器日志。' : '')
  } finally { sessionLoading.value = false }
}

async function login() {
  if (!loginForm.name) return ElMessage.warning('请选择自己的姓名')
  if (sessionOptions.value?.access_code_required && !loginForm.accessCode) return ElMessage.warning('请输入系统访问口令')
  loginLoading.value = true
  try {
    session.value = await api.sessionLogin(loginForm.name, loginForm.accessCode)
    loginForm.accessCode = ''
    ElMessage.success(`欢迎，${session.value.name}`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '无法进入系统，请检查姓名和访问口令。')
  } finally { loginLoading.value = false }
}

async function handleUserCommand(command: string) {
  if (command === 'admin') {
    adminPanel.value = true
    return
  }
  if (command !== 'logout') return
  try {
    await api.sessionLogout()
    session.value = { authenticated: false }
    adminPanel.value = false
    ElMessage.success('已退出当前身份')
  } catch {
    ElMessage.error('退出失败，请检查服务器连接后重试。')
  }
}

function sessionExpired() {
  if (sessionOptions.value?.auth_required && session.value?.authenticated) {
    session.value = { authenticated: false }
    adminPanel.value = false
    ElMessage.warning('登录已过期，请重新选择自己的姓名进入。')
  }
}

function networkStatus(event: Event) {
  const online = Boolean((event as CustomEvent<{ online: boolean }>).detail?.online)
  const wasOffline = !networkOnline.value
  networkOnline.value = online
  if (online && wasOffline) ElMessage.success('服务器连接已恢复，页面数据已刷新。')
}

function browserOffline() {
  networkOnline.value = false
}

async function retryConnection() {
  retrying.value = true
  try {
    await loadSession()
    if (networkOnline.value) window.dispatchEvent(new CustomEvent('jx-data-refresh'))
    else ElMessage.warning('仍无法连接服务器，请稍后再试。')
  } finally {
    retrying.value = false
  }
}

onMounted(() => {
  window.addEventListener('jx-session-expired', sessionExpired)
  window.addEventListener('jx-network-status', networkStatus)
  window.addEventListener('offline', browserOffline)
  window.addEventListener('online', retryConnection)
  loadSession()
})
onBeforeUnmount(() => {
  window.removeEventListener('jx-session-expired', sessionExpired)
  window.removeEventListener('jx-network-status', networkStatus)
  window.removeEventListener('offline', browserOffline)
  window.removeEventListener('online', retryConnection)
})
</script>

<style>
:root {
  color-scheme: light;
  font-family: "Microsoft YaHei", "PingFang SC", system-ui, -apple-system, sans-serif;
  color: #1d2939;
  background: #f4f7fb;
  font-synthesis: none;
}

* {
  box-sizing: border-box;
}

html {
  scroll-behavior: smooth;
}

body {
  margin: 0;
  min-width: 320px;
  background:
    radial-gradient(circle at 80% 0%, rgba(38, 112, 255, 0.08), transparent 26rem),
    #f4f7fb;
}

button,
input,
textarea,
select {
  font: inherit;
}

.layout {
  min-height: 100vh;
}

.session-loading,
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background:
    radial-gradient(circle at 72% 12%, rgba(80, 151, 232, 0.18), transparent 28rem),
    linear-gradient(145deg, #eef4fb 0%, #f8fafc 60%, #eef3f8 100%);
}

.session-loading {
  color: #526579;
}

.login-card {
  width: min(460px, calc(100vw - 30px));
  padding: 36px 38px 32px;
  border: 1px solid #dce7f2;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 24px 70px rgba(36, 72, 111, 0.16);
}

.login-mark {
  width: 52px;
  height: 52px;
  margin-bottom: 18px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  color: #fff;
  background: linear-gradient(145deg, #173e69, #2674bd);
  font-size: 24px;
  font-weight: 800;
  box-shadow: 0 10px 22px rgba(30, 92, 151, 0.22);
}

.login-eyebrow {
  color: #2670b8;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.login-card h1 {
  margin: 7px 0 8px;
  color: #173856;
  font-size: 25px;
}

.login-card > p {
  margin: 0 0 24px;
  color: #63778a;
  font-size: 14px;
  line-height: 1.7;
}

.login-control,
.login-button {
  width: 100%;
}

.login-note {
  margin-top: 18px;
  padding: 10px 12px;
  border-radius: 10px;
  color: #53708a;
  background: #edf5fc;
  font-size: 12px;
  line-height: 1.6;
}

.app-header {
  position: sticky;
  z-index: 100;
  top: 0;
  height: 68px;
  padding: 0 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  background: linear-gradient(112deg, #12345b 0%, #1d4f88 68%, #2265aa 100%);
  box-shadow: 0 4px 18px rgba(25, 61, 105, 0.16);
}

.header-inner {
  width: min(1380px, 100%);
  height: 100%;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: #fff;
  text-decoration: none;
}

.brand-mark {
  width: 38px;
  height: 38px;
  display: inline-grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.36);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.14);
  font-size: 19px;
  font-weight: 800;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2);
}

.brand-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.brand-copy strong {
  letter-spacing: 0.02em;
  font-size: 17px;
}

.brand-copy small {
  color: #c8d9ee;
  font-size: 11px;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #d5e3f4;
  font-size: 12px;
}

.safe-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #61d69b;
  box-shadow: 0 0 0 4px rgba(97, 214, 155, 0.14);
}

.version {
  margin-left: 8px;
  padding: 4px 9px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
}

.user-chip {
  padding: 5px 10px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  color: #eef6ff;
  background: rgba(255, 255, 255, 0.1);
  cursor: pointer;
}

.connection-mark {
  background: linear-gradient(145deg, #9a5b12, #d58a2f);
}

.connection-card > p {
  margin-bottom: 22px;
}

.network-banner {
  position: fixed;
  z-index: 3000;
  top: 10px;
  left: 50%;
  width: min(680px, calc(100vw - 24px));
  padding: 10px 13px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: #743f09;
  border: 1px solid #efc173;
  border-radius: 11px;
  background: #fff4dc;
  box-shadow: 0 10px 30px rgba(90, 60, 20, 0.18);
  transform: translateX(-50%);
  font-size: 12px;
}

.network-banner span {
  flex: 1;
}

.connection-slide-enter-active,
.connection-slide-leave-active {
  transition: 0.2s ease;
}

.connection-slide-enter-from,
.connection-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, -12px);
}

.safe-dot.offline {
  background: #f2ad47;
  box-shadow: 0 0 0 4px rgba(242, 173, 71, 0.18);
}

.app-main {
  width: min(1430px, 100%);
  margin: 0 auto;
  padding: 26px 24px 48px;
  overflow: visible;
}

@media (max-width: 720px) {
  .network-banner {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .network-banner span {
    width: calc(100% - 150px);
  }

  .app-header {
    height: 60px;
    padding: 0 14px;
  }

  .brand-mark {
    width: 34px;
    height: 34px;
  }

  .brand-copy strong {
    font-size: 15px;
  }

  .brand-copy small,
  .safe-text,
  .version {
    display: none;
  }

  .app-main {
    padding: 18px 12px 36px;
  }
}
</style>
