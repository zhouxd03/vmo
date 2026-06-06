<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  NButton, NIcon, NCard, NEmpty, NTag, NSpin, NInput, NPopconfirm,
  NTooltip, NModal, NCheckbox, useMessage,
} from 'naive-ui'
import {
  RefreshOutline, SaveOutline, EyeOutline, CodeSlashOutline,
  AddOutline, TrashOutline, CreateOutline, CheckmarkCircle,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'

const message = useMessage()

const templates = ref({})          // key → template meta (with presets[])
const loading = ref(false)
const activeKey = ref('')
const presetId = ref('')            // currently-edited preset
const draft = ref('')               // edited body
const saving = ref(false)
const rendered = ref('')            // live preview
const previewing = ref(false)

// add / rename preset dialog
const dialog = ref({ show: false, mode: 'add', name: '', clone: true })

const STAGE_ORDER = ['stage1', 'stage2', 'asset', 'continuity', 'video']
const groups = computed(() => {
  const by = {}
  for (const [key, t] of Object.entries(templates.value)) {
    const s = t.stage || 'other'
    ;(by[s] ||= { label: t.stage_label || s, items: [] }).items.push({ key, ...t })
  }
  return STAGE_ORDER.filter((s) => by[s]).map((s) => by[s]).concat(
    Object.keys(by).filter((s) => !STAGE_ORDER.includes(s)).map((s) => by[s]))
})

const active = computed(() => (activeKey.value ? templates.value[activeKey.value] : null))
const presets = computed(() => active.value?.presets || [])
const activePreset = computed(() => presets.value.find((p) => p.id === presetId.value) || presets.value[0] || null)
const isUsing = computed(() => activePreset.value && active.value?.active === activePreset.value.id)
const dirty = computed(() => activePreset.value && draft.value !== activePreset.value.body)

onMounted(load)

async function load() {
  loading.value = true
  try {
    templates.value = await api.listTemplates()
    if (!activeKey.value || !templates.value[activeKey.value]) {
      activeKey.value = Object.keys(templates.value)[0] || ''
    }
    syncPreset()
    syncDraft()
    await refreshPreview()
  } catch (e) { message.error(e.message) } finally { loading.value = false }
}

function syncPreset() {
  const t = active.value
  if (!t) { presetId.value = ''; return }
  if (!t.presets.some((p) => p.id === presetId.value)) presetId.value = t.active || t.presets[0]?.id || ''
}
function syncDraft() { draft.value = activePreset.value?.body || '' }

async function confirmDiscard() {
  return !dirty.value || window.confirm('当前方案有未保存修改，切换将丢弃。是否继续？')
}

async function selectTemplate(key) {
  if (!(await confirmDiscard())) return
  activeKey.value = key
  presetId.value = templates.value[key]?.active || ''
  syncPreset(); syncDraft(); await refreshPreview()
}

async function selectPreset(pid) {
  if (pid === presetId.value) return
  if (!(await confirmDiscard())) return
  presetId.value = pid
  syncDraft(); await refreshPreview()
}

const bodyHtml = computed(() => {
  const esc = (draft.value || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc.replace(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g, '<span class="var">{{$1}}</span>')
})

function insertVar(name) { draft.value = `${draft.value}{{${name}}}` }
function varToken(name) { return `{{${name}}}` }
const VAR_SYNTAX = '{{变量名}}'

async function refreshPreview() {
  if (!activePreset.value) return
  previewing.value = true
  try {
    const samples = {}
    for (const v of activePreset.value.variables || []) samples[v.name] = v.sample
    if (dirty.value) {
      rendered.value = (draft.value || '').replace(
        /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g,
        (_, k) => (samples[k] != null && samples[k] !== '' ? samples[k] : `<${k}>`))
    } else {
      const r = await api.previewTemplate(activeKey.value, {}, presetId.value)
      rendered.value = r.rendered
    }
  } catch (e) { message.error(e.message) } finally { previewing.value = false }
}

function patchTemplate(updated) {
  templates.value = { ...templates.value, [activeKey.value]: updated }
}

async function save() {
  if (!dirty.value) return
  saving.value = true
  try {
    patchTemplate(await api.saveTemplate(activeKey.value, draft.value, presetId.value))
    message.success('方案已保存')
    await refreshPreview()
  } catch (e) { message.error(e.message) } finally { saving.value = false }
}

async function setUsing() {
  try {
    patchTemplate(await api.setActivePreset(activeKey.value, presetId.value))
    message.success('已设为当前使用方案（引擎将采用此方案）')
  } catch (e) { message.error(e.message) }
}

async function resetToDefault() {
  try {
    patchTemplate(await api.resetTemplate(activeKey.value))
    syncPreset(); syncDraft()
    message.success('已恢复内置默认方案')
    await refreshPreview()
  } catch (e) { message.error(e.message) }
}

async function removePreset(p) {
  try {
    patchTemplate(await api.deletePreset(activeKey.value, p.id))
    if (presetId.value === p.id) { presetId.value = active.value.active; syncDraft() }
    message.success('方案已删除')
    await refreshPreview()
  } catch (e) { message.error(e.message) }
}

function openAdd() { dialog.value = { show: true, mode: 'add', name: '', clone: true } }
function openRename() { dialog.value = { show: true, mode: 'rename', name: activePreset.value?.name || '', clone: false } }

async function confirmDialog() {
  const name = (dialog.value.name || '').trim()
  if (!name) { message.warning('请输入方案名称'); return }
  try {
    if (dialog.value.mode === 'add') {
      const body = dialog.value.clone ? (activePreset.value?.body || '') : ''
      const r = await api.addPreset(activeKey.value, name, body, dialog.value.clone ? presetId.value : null)
      patchTemplate(r.template)
      presetId.value = r.preset_id
      syncDraft()
      message.success('已新增方案')
      await refreshPreview()
    } else {
      patchTemplate(await api.renamePreset(activeKey.value, presetId.value, name))
      message.success('已重命名')
    }
    dialog.value.show = false
  } catch (e) { message.error(e.message) }
}
</script>

<template>
  <div>
    <PageHeader title="提示词模板" subtitle="每个模板可保存多套备选方案（题材/风格/模型各异），自由新增·切换·设为使用中；连续性/编号/资产引用由引擎接管">
      <template #actions>
        <n-button size="small" quaternary @click="load">
          <template #icon><n-icon :component="RefreshOutline" /></template>刷新
        </n-button>
      </template>
    </PageHeader>

    <n-spin :show="loading">
      <n-empty v-if="!Object.keys(templates).length && !loading" description="暂无模板" />
      <div v-else class="layout">
        <!-- left: grouped template list -->
        <div class="rail">
          <div v-for="(g, gi) in groups" :key="gi" class="grp">
            <div class="grp-label">{{ g.label }}</div>
            <button
              v-for="t in g.items" :key="t.key"
              class="titem" :class="{ active: t.key === activeKey }"
              @click="selectTemplate(t.key)">
              <span class="tname">{{ t.name }}</span>
              <n-tag v-if="t.presets.length > 1" size="tiny" :bordered="false">{{ t.presets.length }} 套</n-tag>
            </button>
          </div>
        </div>

        <!-- main: editor + variables + preview -->
        <div v-if="active" class="editor">
          <n-card :bordered="false" class="panel">
            <div class="ehead">
              <div>
                <div class="etitle">{{ active.name }}</div>
                <div class="emeta">
                  <n-tag size="tiny" :bordered="false">{{ active.stage_label }}</n-tag>
                  <span class="mono key">{{ activeKey }}</span>
                </div>
              </div>
            </div>

            <!-- preset tabs -->
            <div class="presets">
              <div class="ptabs">
                <button
                  v-for="p in presets" :key="p.id"
                  class="ptab" :class="{ active: p.id === presetId }"
                  @click="selectPreset(p.id)">
                  <span class="pname">{{ p.name }}</span>
                  <n-icon v-if="active.active === p.id" :component="CheckmarkCircle" class="pusing" />
                </button>
                <button class="ptab add" @click="openAdd">
                  <n-icon :component="AddOutline" /> 新增方案
                </button>
              </div>
              <div class="pactions">
                <n-tag v-if="isUsing" size="small" type="success" :bordered="false">
                  <template #icon><n-icon :component="CheckmarkCircle" /></template>当前使用中
                </n-tag>
                <n-button v-else size="tiny" type="primary" ghost @click="setUsing">设为使用中</n-button>
                <n-button size="tiny" quaternary @click="openRename">
                  <template #icon><n-icon :component="CreateOutline" /></template>重命名
                </n-button>
                <n-popconfirm v-if="active.has_default && activePreset?.id === 'default'" @positive-click="resetToDefault">
                  <template #trigger>
                    <n-button size="tiny" quaternary>
                      <template #icon><n-icon :component="RefreshOutline" /></template>恢复默认
                    </n-button>
                  </template>
                  恢复内置默认方案？当前编辑内容将被覆盖。
                </n-popconfirm>
                <n-popconfirm v-if="presets.length > 1 && !activePreset?.is_builtin" @positive-click="removePreset(activePreset)">
                  <template #trigger>
                    <n-button size="tiny" quaternary type="error">
                      <template #icon><n-icon :component="TrashOutline" /></template>删除
                    </n-button>
                  </template>
                  删除方案「{{ activePreset?.name }}」？
                </n-popconfirm>
                <span class="pspacer" />
                <n-tag v-if="dirty" size="tiny" type="warning" :bordered="false">未保存</n-tag>
                <n-button size="small" type="primary" :disabled="!dirty" :loading="saving" @click="save">
                  <template #icon><n-icon :component="SaveOutline" /></template>保存
                </n-button>
              </div>
            </div>

            <!-- variable chips -->
            <div class="vars">
              <div class="vlabel"><n-icon :component="CodeSlashOutline" /> 可用变量（点击插入到末尾）</div>
              <div class="vchips">
                <n-tooltip v-for="v in activePreset?.variables || []" :key="v.name" trigger="hover">
                  <template #trigger>
                    <button class="vchip" @click="insertVar(v.name)">{{ varToken(v.name) }}</button>
                  </template>
                  {{ v.desc || v.name }}（示例：{{ v.sample || '—' }}）
                </n-tooltip>
                <span v-if="!(activePreset?.variables || []).length" class="hint">该方案无变量</span>
              </div>
            </div>

            <!-- body editor -->
            <div class="field">
              <label>方案正文（用 <span class="mono">{{ VAR_SYNTAX }}</span> 占位，运行时由引擎填充）</label>
              <n-input
                v-model:value="draft" type="textarea" :autosize="{ minRows: 10, maxRows: 22 }"
                placeholder="模板正文…" @update:value="refreshPreview" />
            </div>
          </n-card>

          <!-- live preview -->
          <n-card :bordered="false" class="panel">
            <div class="ehead">
              <div class="etitle"><n-icon :component="EyeOutline" /> 实时预览（示例变量填充）</div>
              <n-spin v-if="previewing" :size="14" />
            </div>
            <pre class="preview" v-html="bodyHtml"></pre>
            <div class="plabel">↓ 填充示例变量后的实际请求文本</div>
            <pre class="preview rendered">{{ rendered }}</pre>
          </n-card>
        </div>
      </div>
    </n-spin>

    <!-- add / rename dialog -->
    <n-modal v-model:show="dialog.show" preset="card" style="width: 420px"
      :title="dialog.mode === 'add' ? '新增备选方案' : '重命名方案'">
      <div class="dlg">
        <label>方案名称</label>
        <n-input v-model:value="dialog.name" placeholder="如：科幻硬核风 / 古风文言 / Gemini 优化版"
          @keyup.enter="confirmDialog" />
        <n-checkbox v-if="dialog.mode === 'add'" v-model:checked="dialog.clone" style="margin-top: 12px">
          以当前方案正文为基础（取消则新建空白方案）
        </n-checkbox>
        <div class="dlg-actions">
          <n-button size="small" @click="dialog.show = false">取消</n-button>
          <n-button size="small" type="primary" @click="confirmDialog">确定</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 240px 1fr; gap: 16px; }
@media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }

.rail { display: flex; flex-direction: column; gap: 14px; }
.grp-label { font-size: 11px; color: var(--app-text-muted); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; padding-left: 2px; }
.titem {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  width: 100%; text-align: left; cursor: pointer; margin-bottom: 4px;
  padding: 8px 10px; border-radius: 8px; font-size: 13px;
  background: transparent; border: 1px solid transparent; color: var(--app-text-primary);
}
.titem:hover { background: color-mix(in srgb, var(--app-surface) 70%, transparent); }
.titem.active { background: color-mix(in srgb, var(--app-accent) 14%, transparent); border-color: color-mix(in srgb, var(--app-accent) 40%, transparent); color: var(--app-accent); }
.tname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.editor { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.panel {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card);
}
.ehead { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.etitle { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
.emeta { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.emeta .key { font-size: 11px; color: var(--app-text-muted); }

.presets { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--app-border); }
.ptabs { display: flex; flex-wrap: wrap; gap: 8px; }
.ptab {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 6px 12px; border-radius: var(--r-pill); font-size: 13px;
  background: var(--app-surface-2); border: 1px solid var(--app-border); color: var(--app-text-secondary);
  transition: border-color 0.15s, color 0.15s;
}
.ptab:hover { border-color: var(--app-border-strong); color: var(--app-text-primary); }
.ptab.active { background: color-mix(in srgb, var(--app-accent) 16%, transparent); border-color: var(--app-accent); color: var(--app-accent); }
.ptab.add { color: var(--app-accent); border-style: dashed; background: transparent; }
.pusing { color: var(--app-accent); }
.pactions { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.pspacer { flex: 1; }

.vars { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--app-border); }
.vlabel { font-size: 12px; color: var(--app-text-muted); display: flex; align-items: center; gap: 5px; margin-bottom: 8px; }
.vchips { display: flex; flex-wrap: wrap; gap: 6px; }
.vchip {
  cursor: pointer; font-family: var(--font-mono, monospace); font-size: 12px;
  padding: 3px 8px; border-radius: 6px; color: var(--app-accent);
  background: color-mix(in srgb, var(--app-accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--app-accent) 30%, transparent);
}
.vchip:hover { background: color-mix(in srgb, var(--app-accent) 22%, transparent); }

.field { margin-top: 14px; }
.field > label { display: block; font-size: 12px; color: var(--app-text-muted); margin-bottom: 6px; }
.mono { font-family: var(--font-mono, monospace); }
.hint { font-size: 12px; color: var(--app-text-muted); }

.preview {
  margin: 0; padding: 12px; border-radius: 8px; background: var(--app-bg);
  border: 1px solid var(--app-border); font-family: var(--font-mono, monospace);
  font-size: 12px; line-height: 1.55; white-space: pre-wrap; word-break: break-word;
  max-height: 320px; overflow: auto; color: var(--app-text-secondary);
}
.preview :deep(.var) {
  color: var(--app-accent); font-weight: 600;
  background: color-mix(in srgb, var(--app-accent) 12%, transparent);
  border-radius: 4px; padding: 0 2px;
}
.plabel { font-size: 11px; color: var(--app-text-muted); margin: 10px 0 6px; }
.preview.rendered { color: var(--app-text-primary); }

.dlg > label { display: block; font-size: 13px; color: var(--app-text-secondary); margin-bottom: 8px; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
</style>
