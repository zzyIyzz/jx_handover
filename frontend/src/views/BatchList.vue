<template>
  <div>
    <div class="toolbar">
      <h2>交接班班次</h2>
      <el-button type="primary" @click="showCreate = true">新建交接班</el-button>
    </div>

    <el-table :data="batches" v-loading="loading" border stripe>
      <el-table-column label="交接窗口" min-width="200">
        <template #default="{ row }">
          {{ cnDate(row.start_date) }} ~ {{ cnDate(row.end_date) }}
        </template>
      </el-table-column>
      <el-table-column label="交接班时间" min-width="120">
        <template #default="{ row }">{{ cnDate(row.handover_date) }}</template>
      </el-table-column>
      <el-table-column label="场站" min-width="200">
        <template #default="{ row }">
          <el-tag v-for="s in row.stations" :key="s" size="small" class="mr4">{{ s }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="事项" width="120" align="center">
        <template #default="{ row }">
          共 {{ row.item_total }} · 待复核
          <span :class="{ warn: row.pending_review > 0 }">{{ row.pending_review }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110" align="center">
        <template #default="{ row }">
          <el-tag :type="row.status === 'published' ? 'success' : 'warning'" size="small">
            {{ row.status === 'published' ? '已发布' : '复核中' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="$router.push(`/batch/${row.id}`)">
            进入复核
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" title="新建交接班" width="520px">
      <el-form label-width="110px">
        <el-form-item label="交接开始日期">
          <el-date-picker v-model="form.start_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="交接截止日期">
          <el-date-picker v-model="form.end_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="交接班时间">
          <el-date-picker v-model="form.handover_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="场站">
          <el-select v-model="form.station_ids" multiple style="width: 100%">
            <el-option v-for="s in stations" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建并开始分析</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, cnDate, type BatchSummary, type Station } from '@/api'

const router = useRouter()
const loading = ref(false)
const batches = ref<BatchSummary[]>([])
const stations = ref<Station[]>([])
const showCreate = ref(false)
const creating = ref(false)
const form = reactive({
  start_date: '',
  end_date: '',
  handover_date: '',
  station_ids: [] as number[]
})

async function load() {
  loading.value = true
  try {
    batches.value = await api.listBatches()
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.start_date || !form.end_date || !form.handover_date) {
    ElMessage.warning('请填写完整日期')
    return
  }
  if (!form.station_ids.length) {
    ElMessage.warning('请选择至少一个场站')
    return
  }
  creating.value = true
  try {
    const r = await api.createBatch({ ...form })
    ElMessage.success('班次创建成功，事项分析已完成')
    showCreate.value = false
    router.push(`/batch/${r.id}`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  stations.value = await api.stations()
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.toolbar h2 {
  margin: 0;
}
.mr4 {
  margin-right: 4px;
}
.warn {
  color: #e6a23c;
  font-weight: 700;
}
</style>
