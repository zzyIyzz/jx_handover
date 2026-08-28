<template>
  <el-container class="layout">
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
          <span class="safe-dot"></span>
          <span class="safe-text">数据本地保存</span>
          <span class="version">V0.2 易用版</span>
        </div>
      </div>
    </el-header>
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
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

.app-main {
  width: min(1430px, 100%);
  margin: 0 auto;
  padding: 26px 24px 48px;
  overflow: visible;
}

@media (max-width: 720px) {
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
  .header-meta {
    display: none;
  }

  .app-main {
    padding: 18px 12px 36px;
  }
}
</style>
