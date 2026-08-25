<template>
  <el-container class="layout">
    <el-header class="header">
      <router-link to="/" class="title">江西片区智能交接班系统</router-link>
      <span class="sub">数据库是唯一事实源 · 日期/超期/颜色由程序计算 · AI 无发布权限</span>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
</script>

<style>
body {
  margin: 0;
  background: #f5f7fa;
}
.layout {
  min-height: 100vh;
}
.header {
  background: #1f3a5f;
  display: flex;
  align-items: center;
  gap: 16px;
}
.header .title {
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  text-decoration: none;
}
.header .sub {
  color: #b9c6d8;
  font-size: 12px;
}
</style>
