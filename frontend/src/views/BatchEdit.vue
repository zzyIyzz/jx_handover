<template>
  <div class="batch-page" v-loading="loading">
    <template v-if="batch && currentStation">
      <header class="page-hero">
        <div>
          <el-button link class="back-button" @click="router.push('/')">← 返回工作台</el-button>
          <span class="eyebrow">V0.4.1 · 局域网多人协作</span>
          <h1>{{ currentStation.station_name }}交接班记录</h1>
          <p>{{ cnDate(batch.start_date) }} — {{ cnDate(batch.end_date) }} 班次 · 交接日 {{ cnDate(batch.handover_date) }}</p>
        </div>
        <div class="hero-actions">
          <el-button @click="openImport">导入 Excel</el-button>
          <el-button type="primary" :loading="rendering" @click="generateWord">生成 Word</el-button>
        </div>
      </header>

      <el-tabs v-if="batch.stations.length > 1" v-model="activeMetaId" class="station-tabs">
        <el-tab-pane v-for="station in batch.stations" :key="station.station_meta_id"
                     :name="station.station_meta_id" :label="station.station_name" />
      </el-tabs>

      <nav class="chapter-nav" aria-label="六章导航">
        <a href="#chapter-1"><b>一</b><span>基本信息</span></a>
        <a href="#chapter-2"><b>二</b><span>设备变更</span></a>
        <a href="#chapter-3"><b>三</b><span>重点工作</span></a>
        <a href="#chapter-4"><b>四</b><span>需交接工作</span></a>
        <a href="#chapter-5"><b>五</b><span>外委考核</span></a>
        <a href="#chapter-6"><b>六</b><span>定期工作</span></a>
      </nav>

      <main class="chapter-stack">
        <section id="chapter-1" class="chapter-card">
          <div class="chapter-heading">
            <div><span class="chapter-no">一</span><div><h2>基本信息</h2><p>选择后自动保存；下拉框只显示姓名，也可以直接输入新姓名。</p></div></div>
            <el-tag :type="metaSaving ? 'warning' : 'success'" effect="light" round>
              {{ metaSaving ? '保存中…' : '已自动保存' }}
            </el-tag>
          </div>
          <div class="meta-grid">
            <label><span>交接开始时间</span><el-input :model-value="batch.start_date" disabled /></label>
            <label><span>交接截止时间</span><el-input :model-value="batch.end_date" disabled /></label>
            <label><span>交接班时间</span><el-input :model-value="batch.handover_date" disabled /></label>
            <label>
              <span>值班负责人</span>
              <el-select v-model="currentStation.duty_leader" filterable allow-create default-first-option
                         placeholder="请选择或输入姓名" @change="queueMetaSave">
                <el-option v-for="person in staffList" :key="person.id" :label="person.name" :value="person.name" />
              </el-select>
            </label>
            <label>
              <span>临时值班负责人</span>
              <el-select v-model="currentStation.temp_leader" filterable allow-create default-first-option
                         placeholder="无" @change="queueMetaSave">
                <el-option label="无" value="无" />
                <el-option v-for="person in staffList" :key="person.id" :label="person.name" :value="person.name" />
              </el-select>
            </label>
            <label class="operators-field">
              <span>当班值班员</span>
              <el-select v-model="currentStation.operators" multiple filterable allow-create default-first-option
                         placeholder="请选择或输入姓名" @change="queueMetaSave">
                <el-option v-for="person in staffList" :key="person.id" :label="person.name" :value="person.name" />
              </el-select>
            </label>
          </div>
        </section>

        <section id="chapter-2" class="chapter-card">
          <div class="chapter-heading">
            <div><span class="chapter-no">二</span><div><h2>设备变更情况</h2><p>可快速添加，也可以随时编辑、删除；误删后支持撤销一次。</p></div></div>
            <el-button v-if="deletedDevice" link type="primary" @click="undoDeviceDelete">撤销上次删除</el-button>
          </div>
          <div class="quick-add">
            <el-input v-model="newDevice" clearable placeholder="例如：#1SVG为检修状态" @keyup.enter="addDevice" />
            <el-button type="primary" :disabled="!newDevice.trim()" @click="addDevice">添加</el-button>
          </div>
          <div v-if="currentStation.device_changes.length" class="simple-list">
            <div v-for="(row, index) in currentStation.device_changes" :key="row.id" class="simple-row">
              <span class="row-index">{{ index + 1 }}</span>
              <template v-if="deviceEditingId === row.id">
                <el-input v-model="deviceEditingText" class="grow" @keyup.enter="saveDevice(row)" />
                <el-button type="primary" link @click="saveDevice(row)">保存</el-button>
                <el-button link @click="deviceEditingId = ''">取消</el-button>
              </template>
              <template v-else>
                <span class="grow">{{ row.content }}</span>
                <el-button link type="primary" @click="editDevice(row)">编辑</el-button>
                <el-button link type="danger" @click="deleteDevice(row)">删除</el-button>
              </template>
            </div>
          </div>
          <el-empty v-else description="本班暂无设备变更；确认无变更时可保持为空" :image-size="70" />
        </section>

        <section v-for="definition in itemSections" :id="definition.anchor" :key="definition.key" class="chapter-card">
          <div class="chapter-heading item-heading">
            <div>
              <span class="chapter-no">{{ definition.number }}</span>
              <div><h2>{{ definition.title }}</h2><p>{{ definition.description }}</p></div>
            </div>
            <div class="count-pills">
              <span><b>{{ sectionItems(definition.key).length }}</b> 条</span>
              <span class="pending"><b>{{ sectionPending(definition.key) }}</b> 待复核</span>
            </div>
          </div>
          <div class="section-toolbar">
            <el-input v-model="sectionSearch[definition.key]" clearable placeholder="搜索工作内容、人员或备注" class="search" />
            <div>
              <el-button @click="openImport">导入</el-button>
              <el-button :disabled="sectionPending(definition.key) === 0"
                         @click="approveSection(definition.key)">批量确认</el-button>
              <el-button type="primary" @click="openNewItem(definition.key)">＋ 添加事项</el-button>
            </div>
          </div>

          <el-table v-if="filteredSectionItems(definition.key).length"
                    :data="filteredSectionItems(definition.key)" row-key="id" class="item-table"
                    :row-style="itemRowStyle" @row-dblclick="openEditItem">
            <el-table-column label="顺序" width="78" align="center">
              <template #default="{ row }">
                <div class="order-buttons">
                  <el-button link aria-label="上移" @click.stop="moveItem(row, -1)">↑</el-button>
                  <el-button link aria-label="下移" @click.stop="moveItem(row, 1)">↓</el-button>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="优先级" width="90" align="center">
              <template #default="{ row }"><el-tag :type="priorityTag(row.priority)" effect="light">{{ PRIORITY_LABEL[row.priority] }}</el-tag></template>
            </el-table-column>
            <el-table-column label="工作内容" min-width="300">
              <template #default="{ row }">
                <div class="item-title"><strong>{{ row.title }}</strong><small v-if="row.latest_progress">{{ row.latest_progress }}</small></div>
              </template>
            </el-table-column>
            <el-table-column label="时间" min-width="150">
              <template #default="{ row }"><span>{{ cnDate(row.start_date) }} → {{ cnDate(row.end_date) }}</span></template>
            </el-table-column>
            <el-table-column :label="definition.key === 'important' ? '完成人' : '责任人'" min-width="150">
              <template #default="{ row }">
                <span v-if="definition.key === 'important'">{{ row.completed_by || '—' }}</span>
                <span v-else>{{ row.previous_owner || '—' }} → {{ row.next_owner || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="105" align="center">
              <template #default="{ row }">{{ ITEM_STATUS_LABEL[row.status] }}</template>
            </el-table-column>
            <el-table-column label="复核" width="96" align="center">
              <template #default="{ row }"><el-tag :type="row.review_status === 'approved' ? 'success' : 'warning'" effect="plain">{{ REVIEW_LABEL[row.review_status] }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="178" fixed="right" align="center">
              <template #default="{ row }">
                <el-button v-if="row.review_status !== 'approved'" link type="success" @click.stop="approveItem(row)">确认</el-button>
                <el-button link type="primary" @click.stop="openEditItem(row)">编辑</el-button>
                <el-button link type="danger" @click.stop="deleteItem(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else :description="sectionSearch[definition.key] ? '没有符合搜索条件的事项' : definition.empty" :image-size="72" />
        </section>

        <section id="chapter-5" class="chapter-card">
          <div class="chapter-heading">
            <div><span class="chapter-no">五</span><div><h2>对外委单位的考核</h2><p>字段与原模板完全一致；无数据时 Word 自动保留 1、2、3 三个空白占位行。</p></div></div>
            <el-button type="primary" @click="openNewExternal">＋ 添加考核</el-button>
          </div>
          <el-table v-if="currentStation.external_assessments.length" :data="currentStation.external_assessments" row-key="id">
            <el-table-column label="顺序" width="78" align="center">
              <template #default="{ row }"><div class="order-buttons"><el-button link @click="moveExternal(row, -1)">↑</el-button><el-button link @click="moveExternal(row, 1)">↓</el-button></div></template>
            </el-table-column>
            <el-table-column prop="contractor" label="外委单位" min-width="170" />
            <el-table-column prop="work_content" label="工作内容" min-width="260" />
            <el-table-column prop="assessment" label="考核情况" min-width="220" />
            <el-table-column prop="remark" label="备注" min-width="220" />
            <el-table-column label="操作" width="130" fixed="right" align="center">
              <template #default="{ row }"><el-button link type="primary" @click="openEditExternal(row)">编辑</el-button><el-button link type="danger" @click="deleteExternal(row)">删除</el-button></template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="本班暂无外委考核记录；Word 仍会保留三个空白占位行" :image-size="74">
            <el-button @click="openImport">从 XLSX 导入</el-button>
          </el-empty>
        </section>

        <section id="chapter-6" class="chapter-card">
          <div class="chapter-heading">
            <div><span class="chapter-no">六</span><div><h2>定期工作完成情况</h2><p>原 6.1 月度、6.2 季度始终保留；新增的 6.3 年度仅追加在其后。</p></div></div>
          </div>
          <el-tabs v-model="periodicTab" class="periodic-tabs">
            <el-tab-pane label="6.1 月度定期工作" name="monthly" />
            <el-tab-pane label="6.2 季度定期工作" name="quarterly" />
            <el-tab-pane label="6.3 年度定期工作（新增）" name="yearly" />
          </el-tabs>
          <el-table :data="currentStation.general[periodicTab]" row-key="id" class="periodic-table" :row-style="periodicRowStyle">
            <el-table-column type="index" label="序号" width="68" align="center" />
            <el-table-column prop="title" label="工作内容" min-width="290" />
            <el-table-column label="开始时间" width="125"><template #default="{ row }">{{ cnDate(row.plan_start) }}</template></el-table-column>
            <el-table-column label="结束时间" width="125"><template #default="{ row }">{{ cnDate(row.plan_end) }}</template></el-table-column>
            <el-table-column label="完成情况" width="135">
              <template #default="{ row }"><el-select v-model="row.status" size="small" @change="saveGeneral(row, { status: row.status })"><el-option label="未完成" value="pending" /><el-option label="已完成" value="completed" /></el-select></template>
            </el-table-column>
            <el-table-column label="完成人" min-width="155">
              <template #default="{ row }"><el-select v-model="row.owner" size="small" filterable allow-create default-first-option @change="saveGeneral(row, { owner: row.owner })"><el-option v-for="person in staffList" :key="person.id" :label="person.name" :value="person.name" /></el-select></template>
            </el-table-column>
            <el-table-column label="备注" min-width="250">
              <template #default="{ row }"><el-input v-model="row.note" size="small" @blur="saveGeneral(row, { note: row.note })" /></template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!currentStation.general[periodicTab].length" description="本班时段没有到期的此类定期工作" :image-size="70" />
        </section>

        <section class="publish-card">
          <div>
            <span class="eyebrow">结构校验与版本留存</span>
            <h2>生成正式交接班 Word</h2>
            <p v-if="pendingTotal">还有 {{ pendingTotal }} 条专业事项待复核；确认后才能生成。</p>
            <p v-else>六章顺序、表头、数据行、模板残留和第五章占位行会在生成前自动校验。</p>
          </div>
          <div class="publish-actions">
            <el-button type="primary" size="large" :loading="rendering" :disabled="pendingTotal > 0" @click="generateWord">校验并生成 Word</el-button>
            <el-dropdown v-if="currentStation.snapshots.length">
              <el-button size="large">历史版本（{{ currentStation.snapshots.length }}）⌄</el-button>
              <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="snapshot in currentStation.snapshots" :key="snapshot.id"><a :href="api.downloadUrl(snapshot.id)" target="_blank">V{{ snapshot.version }} · {{ cnDateTime(snapshot.created_at) }}</a></el-dropdown-item></el-dropdown-menu></template>
            </el-dropdown>
          </div>
        </section>
      </main>

      <el-dialog v-model="itemDialog" :title="editingItemId ? '编辑事项' : '添加事项'" width="min(760px, calc(100vw - 24px))" align-center>
        <el-alert title="系统只给建议：已完成事项建议第三章，其他状态建议第四章；你手工选择的章节始终优先。" type="info" :closable="false" show-icon />
        <el-form label-position="top" class="dialog-form">
          <div class="form-grid two">
            <el-form-item label="所属章节" required><el-select v-model="itemDraft.section"><el-option label="三、重点工作完成情况" value="important" /><el-option label="四、需交接的工作" value="handover" /></el-select></el-form-item>
            <el-form-item label="优先级（只控制颜色）" required><el-select v-model="itemDraft.priority"><el-option label="紧急（红）" value="urgent" /><el-option label="重点（黄）" value="important" /><el-option label="普通（白）" value="normal" /></el-select></el-form-item>
          </div>
          <el-form-item label="工作内容" required><el-input v-model="itemDraft.title_snapshot" type="textarea" :rows="2" placeholder="请填写明确、可交接的工作内容" /></el-form-item>
          <div class="form-grid three">
            <el-form-item label="完成状态"><el-select v-model="itemDraft.status"><el-option v-for="(label, key) in ITEM_STATUS_LABEL" :key="key" :label="label" :value="key" /></el-select></el-form-item>
            <el-form-item label="开始时间"><el-date-picker v-model="itemDraft.start_date" type="date" value-format="YYYY-MM-DD" clearable /></el-form-item>
            <el-form-item label="结束时间"><el-date-picker v-model="itemDraft.end_date" type="date" value-format="YYYY-MM-DD" clearable /></el-form-item>
          </div>
          <el-form-item v-if="itemDraft.section === 'important'" label="完成人"><person-select v-model="itemDraft.completed_by" :staff="staffList" /></el-form-item>
          <div v-else class="form-grid two">
            <el-form-item label="交接前责任人"><person-select v-model="itemDraft.previous_owner" :staff="staffList" /></el-form-item>
            <el-form-item label="交接后责任人"><person-select v-model="itemDraft.next_owner" :staff="staffList" /></el-form-item>
          </div>
          <el-form-item label="最新进展 / 备注"><el-input v-model="itemDraft.latest_progress" type="textarea" :rows="3" placeholder="只写补充进展、处理过程、受阻原因或下一步；与工作内容相同的文字无需重复" /></el-form-item>
          <div class="form-grid two">
            <el-form-item label="受阻原因"><el-input v-model="itemDraft.blocker" /></el-form-item>
            <el-form-item label="下一步"><el-input v-model="itemDraft.next_action" /></el-form-item>
          </div>
          <el-collapse v-if="sources.length"><el-collapse-item title="查看原始来源"><div v-for="source in sources" :key="`${source.sheet}-${source.row_no}`" class="source-row"><b>{{ source.sheet }} 第 {{ source.row_no }} 行 · {{ cnDate(source.date) }}</b><pre>{{ source.text }}</pre></div></el-collapse-item></el-collapse>
        </el-form>
        <template #footer><el-button @click="itemDialog = false">取消</el-button><el-button type="primary" :loading="savingItem" @click="saveItem">保存</el-button></template>
      </el-dialog>

      <el-dialog v-model="externalDialog" :title="editingExternalId ? '编辑外委考核' : '添加外委考核'" width="min(660px, calc(100vw - 24px))" align-center>
        <el-form label-position="top" class="dialog-form">
          <el-form-item label="外委单位"><el-input v-model="externalDraft.contractor" /></el-form-item>
          <el-form-item label="工作内容" required><el-input v-model="externalDraft.work_content" type="textarea" :rows="2" /></el-form-item>
          <el-form-item label="考核情况"><el-input v-model="externalDraft.assessment" type="textarea" :rows="2" /></el-form-item>
          <el-form-item label="备注"><el-input v-model="externalDraft.remark" type="textarea" :rows="2" /></el-form-item>
        </el-form>
        <template #footer><el-button @click="externalDialog = false">取消</el-button><el-button type="primary" :loading="savingExternal" @click="saveExternal">保存</el-button></template>
      </el-dialog>

      <el-dialog v-model="importDialog" title="预览导入第三、四、五章" width="min(1180px, calc(100vw - 24px))" top="4vh" destroy-on-close>
        <div v-if="!importPreview" class="import-start">
          <el-alert title="先解析预览，不会直接写入正式数据。实际工作日志和标准模板都可以使用。" type="info" :closable="false" show-icon />
          <a class="template-download" :href="api.handoverTemplateUrl()" target="_blank">下载标准导入模板（第三章、第四章、第五章）</a>
          <el-upload drag :auto-upload="false" accept=".xlsx" :limit="1" :file-list="importFiles"
                     :on-change="onImportFile" :on-remove="clearImportFile" :on-exceed="importExceed">
            <div class="upload-icon">⇧</div><div class="el-upload__text">拖入 XLSX，或 <em>点击选择</em></div>
            <template #tip><div class="el-upload__tip">真实日志只保存在本机数据目录，不会上传 GitHub。</div></template>
          </el-upload>
          <div class="dialog-center"><el-button type="primary" size="large" :loading="parsingImport" :disabled="!importFile" @click="parseImport">解析并预览</el-button></div>
        </div>
        <template v-else>
          <div class="preview-summary">
            <span>解析器：{{ importPreview.parser_key === 'work_log' ? '实际工作日志' : '标准模板' }}</span>
            <span v-if="importPreview.ai.status === 'success'" class="ai-ok">AI：{{ importPreview.ai.model }} 已整理 {{ importPreview.ai.applied || 0 }} 条</span>
            <span v-else-if="importPreview.ai.status === 'fallback'" class="ai-fallback">AI 调用失败，已自动回退</span>
            <span v-else-if="importPreview.ai.status === 'not_configured'">AI 尚未配置，当前使用本地规则</span>
            <span v-else-if="importPreview.ai.status === 'not_needed'">标准模板无需 AI，已按确定性规则解析</span>
            <span>共 {{ importPreview.summary.total }} 条</span><span>第三章 {{ importCount('important') }}</span><span>第四章 {{ importCount('handover') }}</span><span>第五章 {{ importCount('external') }}</span>
          </div>
          <el-alert
            :title="previewAiTitle(importPreview.ai.status)"
            :description="previewAiDescription(importPreview.ai)"
            :type="previewAiType(importPreview.ai.status)"
            :closable="false"
            show-icon
            class="preview-ai-alert"
          />
          <el-alert v-for="warning in importPreview.warnings" :key="`${warning.sheet}-${warning.field}-${warning.reason}`" :title="`${warning.sheet} · ${warning.reason}`" type="warning" :closable="false" show-icon class="preview-warning" />
          <el-tabs v-model="previewTab">
            <el-tab-pane label="三、重点工作" name="important" />
            <el-tab-pane label="四、需交接工作" name="handover" />
            <el-tab-pane label="五、外委考核" name="external" />
          </el-tabs>
          <el-table :data="previewRows" row-key="preview_key" max-height="52vh" class="preview-table">
            <el-table-column label="导入" width="72" align="center"><template #default="{ row }"><el-checkbox v-model="row.include" :disabled="!row.valid" /></template></el-table-column>
            <el-table-column label="来源" width="105"><template #default="{ row }">{{ row.source.sheet }}<br>第 {{ row.source.row_no }} 行</template></el-table-column>
            <el-table-column :label="previewTab === 'external' ? '外委单位 / 工作内容' : '工作内容'" min-width="330"><template #default="{ row }"><template v-if="row.kind === 'external'"><b>{{ row.contractor || '未填外委单位' }}</b><p>{{ row.work_content }}</p></template><template v-else><b>{{ row.title_snapshot }}</b><p>{{ ITEM_STATUS_LABEL[row.status || 'unknown'] }} · {{ PRIORITY_LABEL[row.priority || 'normal'] }} <el-tag v-if="row.ai_enriched" size="small" type="success" effect="plain">AI {{ Math.round((row.ai_confidence || 0) * 100) }}%</el-tag></p></template></template></el-table-column>
            <el-table-column label="提示" min-width="270"><template #default="{ row }"><el-tag v-if="row.duplicate" type="warning">重复，默认跳过</el-tag><div v-for="message in [...row.errors, ...row.warnings]" :key="message" class="row-warning">{{ message }}</div></template></el-table-column>
            <el-table-column label="操作" width="105" fixed="right" align="center"><template #default="{ row }"><el-button link type="primary" @click="openPreviewRow(row)">检查并编辑</el-button></template></el-table-column>
          </el-table>
          <div class="preview-footer"><el-button @click="resetImport">重新选择文件</el-button><span>将导入 {{ importPreview.rows.filter(row => row.include && row.valid && !row.duplicate).length }} 条有效记录</span><el-button type="primary" :loading="committingImport" @click="commitImport">确认导入</el-button></div>
        </template>
      </el-dialog>

      <el-dialog v-model="previewRowDialog" title="检查导入行" width="min(760px, calc(100vw - 24px))" append-to-body>
        <el-form v-if="previewEditing" label-position="top" class="dialog-form">
          <template v-if="previewEditing.kind === 'item'">
            <div class="form-grid three"><el-form-item label="章节"><el-select v-model="previewEditing.section"><el-option label="第三章" value="important" /><el-option label="第四章" value="handover" /></el-select></el-form-item><el-form-item label="状态"><el-select v-model="previewEditing.status"><el-option v-for="(label, key) in ITEM_STATUS_LABEL" :key="key" :label="label" :value="key" /></el-select></el-form-item><el-form-item label="优先级"><el-select v-model="previewEditing.priority"><el-option v-for="(label, key) in PRIORITY_LABEL" :key="key" :label="label" :value="key" /></el-select></el-form-item></div>
            <el-form-item label="工作内容"><el-input v-model="previewEditing.title_snapshot" type="textarea" :rows="2" /></el-form-item>
            <div class="form-grid two"><el-form-item label="开始时间"><el-date-picker v-model="previewEditing.start_date" value-format="YYYY-MM-DD" type="date" clearable /></el-form-item><el-form-item label="结束时间"><el-date-picker v-model="previewEditing.end_date" value-format="YYYY-MM-DD" type="date" clearable /></el-form-item></div>
            <el-form-item v-if="previewEditing.section === 'important'" label="完成人"><person-select v-model="previewEditing.completed_by" :staff="staffList" /></el-form-item>
            <div v-else class="form-grid two"><el-form-item label="交接前责任人"><person-select v-model="previewEditing.previous_owner" :staff="staffList" /></el-form-item><el-form-item label="交接后责任人"><person-select v-model="previewEditing.next_owner" :staff="staffList" /></el-form-item></div>
            <el-form-item label="最新进展 / 备注"><el-input v-model="previewEditing.latest_progress" type="textarea" :rows="4" /></el-form-item>
          </template>
          <template v-else><el-form-item label="外委单位"><el-input v-model="previewEditing.contractor" /></el-form-item><el-form-item label="工作内容"><el-input v-model="previewEditing.work_content" type="textarea" :rows="2" /></el-form-item><el-form-item label="考核情况"><el-input v-model="previewEditing.assessment" type="textarea" :rows="2" /></el-form-item><el-form-item label="备注"><el-input v-model="previewEditing.remark" type="textarea" :rows="2" /></el-form-item></template>
          <el-checkbox v-model="previewEditing.include">确认导入这一行</el-checkbox>
        </el-form>
        <template #footer><el-button type="primary" @click="finishPreviewEdit">完成检查</el-button></template>
      </el-dialog>
    </template>
    <el-empty v-else-if="!loading" description="未找到这个交接班" />
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElOption, ElSelect } from 'element-plus'
import {
  api, cnDate, cnDateTime, COLOR_HEX, ITEM_STATUS_LABEL, PRIORITY_LABEL, REVIEW_LABEL,
  type BatchDetail, type DeviceChangeView, type ExternalAssessmentView, type GeneralItemView,
  type HandoverItemView, type ImportPreview, type ImportPreviewRow, type SourceRow,
  type Staff, type StationDetail
} from '@/api'

const PersonSelect = defineComponent({
  name: 'PersonSelect',
  props: { modelValue: { type: String, default: '' }, staff: { type: Array as () => Staff[], default: () => [] } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => h(ElSelect, {
      modelValue: props.modelValue, filterable: true, allowCreate: true, defaultFirstOption: true,
      placeholder: '请选择或输入姓名', style: 'width:100%',
      'onUpdate:modelValue': (value: string) => emit('update:modelValue', value)
    }, () => props.staff.map(person => h(ElOption, { key: person.id, label: person.name, value: person.name })))
  }
})

const route = useRoute()
const router = useRouter()
const batchId = String(route.params.id)
const loading = ref(true)
const batch = ref<BatchDetail | null>(null)
const activeMetaId = ref('')
const staffList = ref<Staff[]>([])
const metaSaving = ref(false)
let metaTimer: ReturnType<typeof setTimeout> | null = null
let metaSaveSequence = 0
let metaRequestInFlight = false

const itemSections = [
  { key: 'important' as const, anchor: 'chapter-3', number: '三', title: '重点工作完成情况', description: '本班已完成事项建议放这里；紧急红、重点黄、普通白，颜色与章节互不绑定。', empty: '本班无紧急/重点工作' },
  { key: 'handover' as const, anchor: 'chapter-4', number: '四', title: '需交接的工作', description: '未完成、进行中、受阻或待启动事项建议放这里，人工改章始终优先。', empty: '本班暂无需移交下一班的工作' }
]
const sectionSearch = reactive<Record<'important' | 'handover', string>>({ important: '', handover: '' })
const periodicTab = ref<'monthly' | 'quarterly' | 'yearly'>('monthly')
const currentStation = computed(() => batch.value?.stations.find(row => row.station_meta_id === activeMetaId.value) || batch.value?.stations[0] || null)
const pendingTotal = computed(() => currentStation.value?.items.filter(row => row.review_status === 'pending').length || 0)

async function load() {
  loading.value = true
  try {
    batch.value = await api.batchDetail(batchId)
    if (!activeMetaId.value || !batch.value.stations.some(row => row.station_meta_id === activeMetaId.value)) activeMetaId.value = batch.value.stations[0]?.station_meta_id || ''
    await loadStaff()
  } catch (error) {
    ElMessage.error(friendlyError(error, '交接班加载失败'))
  } finally { loading.value = false }
}
async function loadStaff() {
  if (!currentStation.value) return
  staffList.value = await api.staff(currentStation.value.station_code)
}
watch(activeMetaId, loadStaff)

function queueMetaSave() {
  if (metaTimer) clearTimeout(metaTimer)
  const metaId = currentStation.value?.station_meta_id
  if (!metaId) return
  const sequence = ++metaSaveSequence
  metaSaving.value = true
  metaTimer = setTimeout(() => saveMeta(metaId, sequence), 350)
}
async function saveMeta(metaId: string, sequence: number) {
  // Serialize this browser's autosaves. Without this guard, a second edit made
  // while the first request is in flight can reuse the old revision and look
  // like a conflict with another user.
  if (metaRequestInFlight) {
    metaTimer = setTimeout(() => saveMeta(metaId, sequence), 100)
    return
  }
  const station = batch.value?.stations.find(row => row.station_meta_id === metaId)
  if (!station) return
  metaRequestInFlight = true
  let conflicted = false
  try {
    const result = await api.patchMeta(station.station_meta_id, station.revision, {
      duty_leader: station.duty_leader,
      temp_leader: station.temp_leader || '无',
      operators: station.operators
    })
    station.revision = result.revision
  } catch (error) {
    if ((error as any)?.response?.status === 409) {
      conflicted = true
      metaSaveSequence += 1
      if (metaTimer) clearTimeout(metaTimer)
      await ElMessageBox.alert(
        '其他同事刚刚修改了这份基本信息。系统将重新读取服务器上的最新内容，避免覆盖对方的修改；请核对后再填写一次。',
        '检测到多人编辑冲突',
        {
          type: 'warning', confirmButtonText: '读取最新内容', showClose: false,
          closeOnClickModal: false, closeOnPressEscape: false
        }
      )
      await load()
    } else {
      ElMessage.error(friendlyError(error, '基本信息保存失败'))
    }
  }
  finally {
    metaRequestInFlight = false
    if (conflicted) {
      metaSaving.value = false
    } else if (sequence !== metaSaveSequence) {
      const latestId = currentStation.value?.station_meta_id
      if (latestId) {
        const latestSequence = metaSaveSequence
        metaTimer = setTimeout(() => saveMeta(latestId, latestSequence), 50)
      }
    } else {
      metaSaving.value = false
    }
  }
}

const newDevice = ref('')
const deviceEditingId = ref('')
const deviceEditingText = ref('')
const deletedDevice = ref<{ content: string } | null>(null)
async function addDevice() {
  if (!currentStation.value || !newDevice.value.trim()) return
  await api.addDeviceChange(batchId, currentStation.value.station_meta_id, newDevice.value.trim())
  newDevice.value = ''
  await load()
  ElMessage.success('设备变更已添加')
}
function editDevice(row: DeviceChangeView) { deviceEditingId.value = row.id; deviceEditingText.value = row.content }
async function saveDevice(row: DeviceChangeView) {
  if (!deviceEditingText.value.trim()) return ElMessage.warning('内容不能为空')
  await api.patchDeviceChange(row.id, row.revision, deviceEditingText.value.trim())
  deviceEditingId.value = ''
  await load()
}
async function deleteDevice(row: DeviceChangeView) {
  await ElMessageBox.confirm('删除这条设备变更？删除后可撤销一次。', '确认删除', { type: 'warning' })
  await api.deleteDeviceChange(row.id, row.revision)
  deletedDevice.value = { content: row.content }
  await load()
  ElMessage.success('已删除；可点击“撤销上次删除”恢复')
}
async function undoDeviceDelete() {
  if (!currentStation.value || !deletedDevice.value) return
  await api.addDeviceChange(batchId, currentStation.value.station_meta_id, deletedDevice.value.content)
  deletedDevice.value = null
  await load()
  ElMessage.success('已撤销删除')
}

function sectionItems(section: 'important' | 'handover') { return (currentStation.value?.items || []).filter(row => row.section === section).sort((a, b) => a.sort_order - b.sort_order) }
function filteredSectionItems(section: 'important' | 'handover') {
  const key = sectionSearch[section].trim().toLowerCase()
  if (!key) return sectionItems(section)
  return sectionItems(section).filter(row => [row.title, row.latest_progress, row.completed_by, row.previous_owner, row.next_owner].join(' ').toLowerCase().includes(key))
}
function sectionPending(section: 'important' | 'handover') { return sectionItems(section).filter(row => row.review_status === 'pending').length }
function itemRowStyle({ row }: { row: HandoverItemView }) { return { background: COLOR_HEX[row.color] || '#fff' } }
function priorityTag(priority: string) { return priority === 'urgent' ? 'danger' : priority === 'important' ? 'warning' : 'info' }
async function approveItem(row: HandoverItemView) { await api.approveItem(row.id, row.revision); await load(); ElMessage.success('已确认') }
async function approveSection(section: 'important' | 'handover') {
  if (!currentStation.value) return
  const result = await api.approveAll(batchId, currentStation.value.station_meta_id, section)
  await load(); ElMessage.success(`已确认 ${result.approved} 条`)
}
async function deleteItem(row: HandoverItemView) {
  await ElMessageBox.confirm(`删除“${row.title}”？此操作不会删除历史 Word。`, '确认删除', { type: 'warning' })
  await api.deleteItem(row.id, row.revision); await load(); ElMessage.success('事项已删除')
}
async function moveItem(row: HandoverItemView, direction: -1 | 1) {
  if (!currentStation.value) return
  const rows = sectionItems(row.section)
  const index = rows.findIndex(item => item.id === row.id)
  const target = index + direction
  if (index < 0 || target < 0 || target >= rows.length) return
  ;[rows[index], rows[target]] = [rows[target], rows[index]]
  await api.reorderItems(batchId, currentStation.value.station_meta_id, row.section, rows.map(item => item.id))
  await load()
}

const itemDialog = ref(false)
const editingItemId = ref('')
const editingRevision = ref(0)
const savingItem = ref(false)
const sources = ref<SourceRow[]>([])
const blankItem = () => ({ section: 'handover' as 'important' | 'handover', title_snapshot: '', status: 'pending', priority: 'normal', completed_by: '', previous_owner: '', next_owner: '', start_date: batch.value?.start_date || '', end_date: '', summary: '', latest_progress: '', blocker: '', next_action: '' })
const itemDraft = reactive(blankItem())
function openNewItem(section: 'important' | 'handover') {
  editingItemId.value = ''; editingRevision.value = 0; sources.value = []
  Object.assign(itemDraft, blankItem(), { section, status: section === 'important' ? 'completed' : 'pending' })
  itemDialog.value = true
}
async function openEditItem(row: HandoverItemView) {
  editingItemId.value = row.id; editingRevision.value = row.revision
  Object.assign(itemDraft, { section: row.section, title_snapshot: row.title, status: row.status, priority: row.priority, completed_by: row.completed_by, previous_owner: row.previous_owner, next_owner: row.next_owner, start_date: row.start_date || '', end_date: row.end_date || '', summary: row.summary, latest_progress: row.latest_progress, blocker: row.blocker, next_action: row.next_action })
  sources.value = []
  itemDialog.value = true
  if (row.work_item_id) try { sources.value = await api.itemSources(row.work_item_id) } catch { /* optional trace */ }
}
async function saveItem() {
  if (!currentStation.value || !itemDraft.title_snapshot.trim()) return ElMessage.warning('请填写工作内容')
  savingItem.value = true
  try {
    const fields = { ...itemDraft, start_date: itemDraft.start_date || null, end_date: itemDraft.end_date || null }
    if (editingItemId.value) await api.patchItem(editingItemId.value, editingRevision.value, fields)
    else await api.addItem(batchId, { station_meta_id: currentStation.value.station_meta_id, ...fields })
    itemDialog.value = false; await load(); ElMessage.success('事项已保存')
  } catch (error) { ElMessage.error(friendlyError(error, '事项保存失败')); if ((error as any)?.response?.status === 409) await load() }
  finally { savingItem.value = false }
}

const externalDialog = ref(false)
const editingExternalId = ref('')
const editingExternalRevision = ref(0)
const savingExternal = ref(false)
const externalDraft = reactive({ contractor: '', work_content: '', assessment: '', remark: '' })
function openNewExternal() { editingExternalId.value = ''; Object.assign(externalDraft, { contractor: '', work_content: '', assessment: '', remark: '' }); externalDialog.value = true }
function openEditExternal(row: ExternalAssessmentView) { editingExternalId.value = row.id; editingExternalRevision.value = row.revision; Object.assign(externalDraft, row); externalDialog.value = true }
async function saveExternal() {
  if (!currentStation.value || !externalDraft.work_content.trim()) return ElMessage.warning('请填写工作内容')
  savingExternal.value = true
  try {
    if (editingExternalId.value) await api.patchExternal(editingExternalId.value, editingExternalRevision.value, { ...externalDraft })
    else await api.addExternal(batchId, { station_meta_id: currentStation.value.station_meta_id, ...externalDraft })
    externalDialog.value = false; await load(); ElMessage.success('外委考核已保存')
  } catch (error) { ElMessage.error(friendlyError(error, '保存失败')) }
  finally { savingExternal.value = false }
}
async function deleteExternal(row: ExternalAssessmentView) { await ElMessageBox.confirm('删除这条外委考核记录？', '确认删除', { type: 'warning' }); await api.deleteExternal(row.id, row.revision); await load() }
async function moveExternal(row: ExternalAssessmentView, direction: -1 | 1) {
  if (!currentStation.value) return
  const rows = [...currentStation.value.external_assessments].sort((a, b) => a.sort_order - b.sort_order)
  const index = rows.findIndex(item => item.id === row.id); const target = index + direction
  if (index < 0 || target < 0 || target >= rows.length) return
  ;[rows[index], rows[target]] = [rows[target], rows[index]]
  await api.reorderExternal(batchId, currentStation.value.station_meta_id, rows.map(item => item.id)); await load()
}

function periodicRowStyle({ row }: { row: GeneralItemView }) { return { background: row.color === 'red' ? '#FFEBEE' : row.color === 'green' ? '#E7F7ED' : '#fff' } }
async function saveGeneral(row: GeneralItemView, fields: Record<string, unknown>) {
  try { const result = await api.patchGeneralItem(row.id, row.revision, fields); row.revision = result.revision }
  catch (error) { ElMessage.error(friendlyError(error, '定期工作保存失败')); await load() }
}

const rendering = ref(false)
async function generateWord() {
  if (!currentStation.value) return
  if (pendingTotal.value) return ElMessage.warning(`还有 ${pendingTotal.value} 条事项待复核`)
  rendering.value = true
  try {
    const result = await api.render(batchId, currentStation.value.station_meta_id)
    const anchor = document.createElement('a'); anchor.href = api.downloadUrl(result.snapshot_id); anchor.target = '_blank'; anchor.click()
    await load(); ElMessage.success(`Word V${result.version} 已通过结构校验并生成`)
  } catch (error) { ElMessage.error(friendlyError(error, 'Word 生成失败')) }
  finally { rendering.value = false }
}

const importDialog = ref(false)
const importFile = ref<File | null>(null)
const importFiles = ref<any[]>([])
const parsingImport = ref(false)
const committingImport = ref(false)
const importPreview = ref<ImportPreview | null>(null)
const previewTab = ref<'important' | 'handover' | 'external'>('important')
const previewRows = computed(() => (importPreview.value?.rows || []).filter(row => row.kind === 'external' ? previewTab.value === 'external' : row.section === previewTab.value))
const previewRowDialog = ref(false)
const previewEditing = ref<ImportPreviewRow | null>(null)
function openImport() { resetImport(); importDialog.value = true }
function onImportFile(upload: any) { if (!upload.raw?.name.toLowerCase().endsWith('.xlsx')) { ElMessage.warning('请选择 .xlsx 文件'); return } importFile.value = upload.raw; importFiles.value = [upload] }
function clearImportFile() { importFile.value = null; importFiles.value = [] }
function importExceed() { ElMessage.info('一次选择一个文件') }
function resetImport() { importFile.value = null; importFiles.value = []; importPreview.value = null; previewTab.value = 'important' }
async function parseImport() {
  if (!currentStation.value || !importFile.value) return
  parsingImport.value = true
  try { importPreview.value = await api.previewImport(batchId, currentStation.value.station_meta_id, importFile.value); previewTab.value = importPreview.value.summary.important ? 'important' : importPreview.value.summary.handover ? 'handover' : 'external'; ElMessage.success('解析完成，请检查预览') }
  catch (error) { ElMessage.error(friendlyError(error, '文件解析失败')) }
  finally { parsingImport.value = false }
}
function importCount(section: 'important' | 'handover' | 'external') { return (importPreview.value?.rows || []).filter(row => section === 'external' ? row.kind === 'external' : row.kind === 'item' && row.section === section).length }
function previewAiType(status: string): 'success' | 'warning' | 'info' | 'error' {
  if (status === 'success') return 'success'
  if (status === 'fallback' || status === 'not_configured') return 'warning'
  if (status === 'not_needed') return 'info'
  return 'error'
}
function previewAiTitle(status: string) {
  if (status === 'success') return 'Qwen 智能整理成功，请继续人工确认'
  if (status === 'fallback') return 'Qwen 调用失败，已自动改用本地规则，仍可继续导入'
  if (status === 'not_configured') return '服务器尚未配置 Qwen API Key，当前使用本地规则'
  if (status === 'not_needed') return '标准模板已按固定字段解析，无需调用 AI'
  return 'AI 状态异常，预览数据仍保留；请人工检查后继续'
}
function previewAiDescription(ai: ImportPreview['ai']) {
  if (ai.status === 'success') return `${ai.model || 'Qwen'} 只整理了当前班次预览中的候选事项，共应用 ${ai.applied || 0} 条建议；人工修改始终优先。`
  if (ai.status === 'fallback') return `本次未采用 AI 建议，确定性解析结果没有丢失。${ai.error ? `错误摘要：${ai.error}` : ''}`
  if (ai.status === 'not_configured') return '无需等待管理员配置，先检查本地规则生成的章节、状态、日期和责任人即可。'
  if (ai.status === 'not_needed') return '文件字段与标准模板一致，系统没有向模型发送数据。'
  return '系统不会因为 AI 异常阻断导入；无效行仍会单独标出，其他有效行可以提交。'
}
function openPreviewRow(row: ImportPreviewRow) { previewEditing.value = row; previewRowDialog.value = true }
function finishPreviewEdit() {
  const row = previewEditing.value
  if (!row) return
  const errors: string[] = []
  if (row.kind === 'item') {
    if (!row.title_snapshot?.trim()) errors.push('工作内容不能为空')
    if (row.section === 'important' && !row.completed_by?.trim()) errors.push('第三章事项缺少完成人')
  } else {
    if (!row.contractor?.trim()) errors.push('外委单位不能为空')
    if (!row.work_content?.trim()) errors.push('工作内容不能为空')
    if (!row.assessment?.trim()) errors.push('考核情况不能为空')
  }
  row.errors = errors; row.valid = errors.length === 0; row.duplicate = false
  previewRowDialog.value = false
}
async function commitImport() {
  if (!importPreview.value) return
  committingImport.value = true
  try { const result = await api.commitImport(batchId, importPreview.value.id, importPreview.value.rows); importDialog.value = false; await load(); ElMessage.success(`导入完成：新增 ${result.created_items || 0} 条事项、${result.created_external_assessments || 0} 条外委考核，跳过 ${result.skipped || 0} 条`) }
  catch (error) { ElMessage.error(friendlyError(error, '确认导入失败')) }
  finally { committingImport.value = false }
}

function friendlyError(error: unknown, fallback: string) {
  const value = error as any; const detail = value?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message && Array.isArray(detail.errors)) return `${detail.message}：${detail.errors.join('；')}`
  if (detail?.message) return detail.message
  if (Array.isArray(detail)) return detail.map(item => item.msg).join('；')
  return value?.message || fallback
}

function refreshAfterReconnect() { load() }

onMounted(() => {
  window.addEventListener('jx-data-refresh', refreshAfterReconnect)
  load()
})
onBeforeUnmount(() => {
  window.removeEventListener('jx-data-refresh', refreshAfterReconnect)
  if (metaTimer) clearTimeout(metaTimer)
})
</script>

<style scoped>
.batch-page { min-width: 0; }
.page-hero { padding: 26px 30px; display: flex; justify-content: space-between; gap: 24px; color: #fff; border-radius: 20px; background: linear-gradient(120deg, #173b66, #2466a7); box-shadow: 0 14px 34px rgba(24, 69, 115, .18); }
.page-hero h1 { margin: 7px 0 8px; font-size: clamp(24px, 3vw, 32px); }
.page-hero p { margin: 0; color: #d8e6f4; }
.back-button { margin: 0 14px 0 -3px; color: #dbeafe !important; }
.eyebrow { color: #bcd6f0; font-size: 12px; font-weight: 800; letter-spacing: .1em; }
.hero-actions { align-self: center; display: flex; flex-shrink: 0; }
.station-tabs { margin: 16px 0 0; padding: 0 20px; border: 1px solid #e4ebf3; border-radius: 14px; background: #fff; }
.chapter-nav { position: sticky; z-index: 20; top: 82px; margin: 16px 0; padding: 9px; display: grid; grid-template-columns: repeat(6, 1fr); gap: 7px; border: 1px solid #e4ebf3; border-radius: 15px; background: rgba(255,255,255,.94); box-shadow: 0 7px 20px rgba(36, 63, 94, .08); backdrop-filter: blur(10px); }
.chapter-nav a { min-width: 0; padding: 9px 10px; display: flex; align-items: center; gap: 8px; color: #53677f; border-radius: 10px; text-decoration: none; font-size: 12px; }
.chapter-nav a:hover { color: #1f5f9f; background: #edf5fd; }
.chapter-nav b { width: 25px; height: 25px; display: grid; place-items: center; flex: 0 0 auto; color: #fff; border-radius: 8px; background: #2d6fac; }
.chapter-nav span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chapter-stack { display: grid; gap: 16px; }
.chapter-card, .publish-card { scroll-margin-top: 158px; padding: 24px; border: 1px solid #e3ebf4; border-radius: 17px; background: #fff; box-shadow: 0 7px 23px rgba(29, 58, 92, .055); }
.chapter-heading { margin-bottom: 18px; display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.chapter-heading > div:first-child { min-width: 0; display: flex; gap: 13px; }
.chapter-no { width: 38px; height: 38px; display: grid; place-items: center; flex: 0 0 auto; color: #fff; border-radius: 11px; background: #245f98; font-weight: 800; }
.chapter-heading h2 { margin: 0; color: #1e3651; font-size: 20px; }
.chapter-heading p { margin: 5px 0 0; color: #7a889a; font-size: 12px; line-height: 1.6; }
.meta-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }
.meta-grid label { min-width: 0; display: grid; gap: 7px; }
.meta-grid label > span { color: #53677e; font-size: 12px; font-weight: 700; }
.operators-field { grid-column: span 2; }
.meta-grid :deep(.el-select), .dialog-form :deep(.el-select), .dialog-form :deep(.el-date-editor) { width: 100%; }
.quick-add { display: flex; gap: 10px; }
.simple-list { margin-top: 14px; display: grid; gap: 8px; }
.simple-row { min-width: 0; padding: 11px 12px; display: flex; align-items: center; gap: 8px; border: 1px solid #e8edf3; border-radius: 10px; background: #f9fbfd; }
.row-index { width: 27px; height: 27px; display: grid; place-items: center; flex: 0 0 auto; color: #3a6e9f; border-radius: 8px; background: #e7f1fb; font-size: 12px; font-weight: 800; }
.grow { min-width: 0; flex: 1; }
.item-heading { align-items: center; }
.count-pills { display: flex; gap: 8px; }
.count-pills span { padding: 7px 11px; color: #52687e; border-radius: 999px; background: #edf4fb; font-size: 12px; white-space: nowrap; }
.count-pills .pending { color: #936018; background: #fff2db; }
.section-toolbar { margin-bottom: 13px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.section-toolbar .search { width: min(360px, 40%); }
.section-toolbar > div { display: flex; gap: 8px; }
.item-table { border-radius: 11px; overflow: hidden; }
.item-table :deep(th.el-table__cell), .periodic-table :deep(th.el-table__cell), .preview-table :deep(th.el-table__cell) { color: #617286; background: #f6f9fc; font-size: 12px; }
.item-title { display: grid; gap: 4px; }
.item-title strong { color: #273e57; line-height: 1.55; }
.item-title small { max-width: 590px; overflow: hidden; color: #76869a; text-overflow: ellipsis; white-space: nowrap; }
.order-buttons { display: flex; justify-content: center; }
.periodic-tabs { margin-top: -5px; }
.publish-card { padding: 26px 28px; display: flex; align-items: center; justify-content: space-between; gap: 24px; color: #fff; background: linear-gradient(116deg, #173b66, #225d96); }
.publish-card h2 { margin: 6px 0 7px; }
.publish-card p { margin: 0; color: #c9dbee; font-size: 13px; }
.publish-actions { display: flex; gap: 9px; flex-shrink: 0; }
.publish-actions a { color: #315f8b; text-decoration: none; }
.dialog-form { margin-top: 15px; }
.form-grid { display: grid; gap: 14px; }
.form-grid.two { grid-template-columns: repeat(2, 1fr); }
.form-grid.three { grid-template-columns: repeat(3, 1fr); }
.source-row { margin: 8px 0; padding: 10px; border-radius: 8px; background: #f6f8fb; }
.source-row pre { margin: 7px 0 0; overflow: auto; color: #5e6e80; white-space: pre-wrap; font: 12px/1.6 "Microsoft YaHei"; }
.import-start { display: grid; gap: 16px; }
.template-download { justify-self: start; color: #1769b1; font-weight: 700; text-decoration: none; }
.upload-icon { color: #5c86ae; font-size: 38px; }
.dialog-center { text-align: center; }
.preview-summary { margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; }
.preview-summary span { padding: 6px 10px; color: #47627e; border-radius: 999px; background: #edf4fb; font-size: 12px; }
.preview-summary .ai-ok { color: #17623a; background: #e7f7ee; }
.preview-summary .ai-fallback { color: #9a5a0a; background: #fff4dc; }
.preview-ai-alert { margin-bottom: 10px; }
.preview-warning { margin-bottom: 8px; }
.preview-table p { margin: 5px 0 0; color: #7a899a; font-size: 12px; }
.row-warning { margin-top: 5px; color: #a36a14; font-size: 11px; line-height: 1.45; }
.preview-footer { padding-top: 16px; display: flex; align-items: center; justify-content: flex-end; gap: 14px; color: #607186; font-size: 12px; }

@media (max-width: 1000px) {
  .chapter-nav { grid-template-columns: repeat(3, 1fr); top: 72px; }
  .meta-grid { grid-template-columns: repeat(2, 1fr); }
  .operators-field { grid-column: span 2; }
}
@media (max-width: 700px) {
  .page-hero, .chapter-heading, .section-toolbar, .publish-card { flex-direction: column; }
  .page-hero { padding: 22px 18px; }
  .hero-actions, .hero-actions .el-button, .publish-actions, .publish-actions .el-button { width: 100%; }
  .chapter-nav { position: static; grid-template-columns: repeat(2, 1fr); }
  .chapter-card { padding: 18px 13px; }
  .meta-grid, .form-grid.two, .form-grid.three { grid-template-columns: 1fr; }
  .operators-field { grid-column: span 1; }
  .section-toolbar .search { width: 100%; }
  .section-toolbar > div { width: 100%; flex-wrap: wrap; }
  .publish-actions { flex-direction: column; }
  .preview-footer { align-items: stretch; flex-direction: column; }
}
</style>
