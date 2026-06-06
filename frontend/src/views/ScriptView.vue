<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NCard, NTag, NEmpty, NSpace, NProgress, NSpin,
  NCollapse, NCollapseItem, NDataTable, NAlert, NInput, NRadioGroup, NRadioButton,
  NTooltip, NPopconfirm, useMessage,
} from 'naive-ui'
import {
  SparklesOutline, GitBranchOutline, PersonOutline, LocationOutline, CubeOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import EpisodeBar from '../components/EpisodeBar.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'
import { useTasksStore } from '../stores/tasks'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()
const tasks = useTasksStore()

// analyze/decompose run in the global tasks store so they keep polling and
// stay live across tab switches (§8.2). These are derived views of store state.
const analyzing = computed(() => tasks.isAnalyzing(store.currentId, store.currentEpisodeId))
const decomposing = computed(() => tasks.isDecomposing(store.currentId, store.currentEpisodeId))
const stageBusy = computed(() => analyzing.value || decomposing.value)
const progressPct = computed(() => tasks.decomposePercent)
const analyzeIntent = ref('initial')

// ── 阶段二：分镜模式（自动/手动）+ 分镜模式预设（按剧本类型，可在「提示词模板」新增）──
const decomposeMode = ref('auto')        // 'auto' | 'manual'
const presetOptions = ref([])            // batch_decompose 的多套提示词预设
const selectedPreset = ref(null)         // 选中的分镜模式预设 id
const manualText = ref('')               // 手动分镜：每段（空行分隔）= 一镜

async function loadDecomposePresets() {
  try {
    const tpls = await api.listTemplates()
    const bd = tpls?.batch_decompose
    if (bd?.presets?.length) {
      presetOptions.value = bd.presets.map((p) => ({ label: p.name, value: p.id }))
      if (!selectedPreset.value || !bd.presets.some((p) => p.id === selectedPreset.value)) {
        selectedPreset.value = bd.active || bd.presets[0].id
      }
    }
  } catch { /* 模板拉取失败不阻塞拆解（后端用 active 预设兜底） */ }
}

// 手动分镜：按空行把原文切成「一段=一镜」的片段
const manualSegments = computed(() =>
  manualText.value.split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean))

// 切到手动模式时，用本集原文预填，方便人工在此切分（增删空行=调整分镜边界）
watch(decomposeMode, (m) => {
  if (m === 'manual' && !manualText.value.trim()) {
    const ep = store.currentEpisode
    const segs = (ep?.segments || []).map((s) => (s.text || '').trim()).filter(Boolean)
    if (segs.length) {
      manualText.value = segs.join('\n\n')
    } else {
      const raw = ep?.raw_text || ''
      manualText.value = raw
        ? raw.split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean).join('\n\n')
        : ''
    }
  }
})

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) {
    await store.select(store.projects[0].id)
  }
  await loadDecomposePresets()
})

// Surface success/failure toasts when a job this view started finishes (the
// store keeps polling even if we navigate away, so the toast only shows if we
// happen to be on this page when it lands — data is reloaded by the store).
watch(() => tasks.decomposeStatus, (s, prev) => {
  if (prev !== 'running' || tasks.decomposeProjectId !== store.currentId) return
  if (s === 'done') message.success(`分批拆解完成，共 ${shots.value.length} 个分镜`)
  else if (s === 'error') message.error(formatStageError(tasks.decomposeError, '分镜拆解'))
})
watch(() => tasks.analyzeStatus, (s, prev) => {
  if (prev !== 'running' || tasks.analyzeProjectId !== store.currentId) return
  if (s === 'done') {
    if (analyzeIntent.value === 'replace') message.success('全局分析完成，已覆盖本集分析并清空旧分镜，请重新拆解')
    else if (analyzeIntent.value === 'append') message.success('本集分析完成，已接入长线故事圣经')
    else message.success('全局分析完成，已建立故事圣经')
  }
  else if (s === 'error') message.error(formatStageError(tasks.analyzeError, '全局分析'))
})

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

async function onSelect(pid) {
  await store.select(pid)
}

const bible = computed(() => store.current?.story_bible)
const shots = computed(() => store.currentEpisode?.shots || [])
const currentEpisode = computed(() => store.currentEpisode)
const episodeStage = computed(() => currentEpisode.value?.stage || 'imported')
const episodeAnalyzed = computed(() =>
  ['analyzed', 'decomposed'].includes(episodeStage.value)
  || !!currentEpisode.value?.story_bible
  || shots.value.length > 0)
const hasSharedBible = computed(() => !!bible.value)
const analyzeButtonText = computed(() => {
  if (episodeAnalyzed.value) return '重新分析本集'
  return hasSharedBible.value ? '分析本集并接入长线圣经' : '开始全局分析'
})
const analyzeDesc = computed(() => {
  if (episodeAnalyzed.value) return '覆盖本集分析结果，并清空本集旧分镜与连续性缓存。'
  if (hasSharedBible.value) return '作为续集首次分析：提取本集新增角色/场景/道具，并并入全集共享故事圣经。'
  return '首集/首轮分析：建立全集共享故事圣经，作为后续分集和资产库的基础。'
})
const canDecompose = computed(() => !!hasSharedBible.value && !!episodeAnalyzed.value && !stageBusy.value)
const decomposeDisabledReason = computed(() => {
  if (analyzing.value) return '全局分析进行中，完成后再拆解。'
  if (!hasSharedBible.value) return '请先完成全局分析，建立故事圣经。'
  if (!episodeAnalyzed.value) return '当前分集尚未分析。续集需要先分析本集，再拆解。'
  return ''
})
function formatStageError(raw, stage) {
  const text = String(raw || '').trim()
  if (!text) return ''
  if (/请先分析当前分集|尚未分析|先分析/.test(text)) {
    return '当前分集还没有接入故事圣经。请先执行「分析本集」，完成后再拆解分镜。'
  }
  if (/请先完成全局分析|story_bible|StoryBible|Stage 1/i.test(text)) {
    return '项目还没有故事圣经。请先完成阶段一全局分析。'
  }
  if (/manual|手动|分镜片段|segment/i.test(text)) {
    return '手动分镜内容为空或格式不正确。请用空行分隔每个分镜片段。'
  }
  if (/Failed to fetch|NetworkError|Load failed|timeout|超时/i.test(text)) {
    return '请求没有成功到达后端或等待超时。请确认后端服务仍在运行，再重试。'
  }
  return `${stage}失败：${text}`
}
const analyzeErrorDetail = computed(() =>
  tasks.analyzeStatus === 'error' && tasks.analyzeProjectId === store.currentId
    ? formatStageError(tasks.analyzeError, '全局分析')
    : '')
const decomposeErrorDetail = computed(() =>
  tasks.decomposeStatus === 'error' && tasks.decomposeProjectId === store.currentId
    ? formatStageError(tasks.decomposeError, '分镜拆解')
    : '')
const stageErrorDetail = computed(() => analyzeErrorDetail.value || decomposeErrorDetail.value)

function clearStageError() {
  if (tasks.analyzeStatus === 'error') tasks.analyzeStatus = 'idle'
  if (tasks.decomposeStatus === 'error') tasks.decomposeStatus = 'idle'
  tasks.analyzeError = ''
  tasks.decomposeError = ''
}

const characterQuery = ref('')
const showAllCharacters = ref(false)
const visibleCharacters = computed(() => {
  const list = bible.value?.characters || []
  const q = characterQuery.value.trim().toLowerCase()
  const filtered = q
    ? list.filter((c) => [c.name, c.alias, c.bio, c.note, c.appearance, c.first_appearance].filter(Boolean).join(' ').toLowerCase().includes(q))
    : list
  return showAllCharacters.value ? filtered : filtered.slice(0, 8)
})
const hiddenCharacterCount = computed(() => Math.max(0, (bible.value?.characters?.length || 0) - visibleCharacters.value.length))
const characterGroups = computed(() => {
  const groups = {}
  for (const c of visibleCharacters.value) {
    const key = (c.first_appearance || '未标注首次出现').split('·')[0]
    ;(groups[key] ||= []).push(c)
  }
  return Object.entries(groups).map(([k, items]) => ({ key: k, items }))
})

// 风格 {{style}} 可手动设定：草稿与圣经保持同步，失焦/回车保存。
const styleDraft = ref('')
const savingStyle = ref(false)
watch(() => bible.value?.style, (v) => { styleDraft.value = v || '' }, { immediate: true })

async function saveStyle() {
  if (!store.current) return
  const next = (styleDraft.value || '').trim()
  if (next === (bible.value?.style || '')) return
  savingStyle.value = true
  try {
    await api.updateStoryBible(store.current.id, { style: next })
    await store.reloadCurrent()
    message.success(next ? '风格已更新（全流程沿用此值）' : '已清空风格，将回退 AI 缺省')
  } catch (e) {
    message.error('风格保存失败: ' + e.message)
  } finally {
    savingStyle.value = false
  }
}

async function runAnalyze() {
  if (!store.current) return
  if (analyzing.value) return
  if (decomposing.value) {
    message.warning('分批拆解进行中，完成后再重新分析')
    return
  }
  analyzeIntent.value = episodeAnalyzed.value ? 'replace' : (hasSharedBible.value ? 'append' : 'initial')
  await tasks.runAnalyze(store.current.id, store.currentEpisodeId)
}

async function runDecompose() {
  if (!store.current) return
  if (decomposing.value) return
  if (!canDecompose.value) {
    message.warning(decomposeDisabledReason.value || '当前状态不能拆解')
    return
  }
  if (decomposeMode.value === 'manual') {
    if (!manualSegments.value.length) {
      message.warning('请先在下方文本框用「空行」分隔出至少一个分镜片段')
      return
    }
    await tasks.startDecompose(store.current.id, store.currentEpisodeId, {
      mode: 'manual', manual_segments: manualSegments.value,
    })
  } else {
    await tasks.startDecompose(store.current.id, store.currentEpisodeId, {
      mode: 'auto', template_preset: selectedPreset.value,
    })
  }
}

const shotColumns = [
  { title: '编号', key: 'shot_no', width: 92 },
  { title: '场景', key: 'scene', width: 110, ellipsis: { tooltip: true } },
  { title: '角色', key: 'characters', width: 120, render: (r) => (r.characters || []).join(' ') },
  { title: '动作', key: 'action', ellipsis: { tooltip: true } },
  { title: '机位', key: 'camera', width: 130, ellipsis: { tooltip: true } },
  { title: '对白', key: 'dialogue', width: 140, ellipsis: { tooltip: true } },
  { title: '接口(handoff)', key: 'handoff', ellipsis: { tooltip: true } },
]
</script>

<template>
  <div>
    <PageHeader title="剧本解析" subtitle="两阶段 LLM：全局分析（故事圣经）→ 分批拆解为结构化分镜">
      <template #actions>
        <n-select
          :value="store.currentId"
          :options="projectOptions"
          placeholder="选择小说项目"
          style="width: 320px"
          @update:value="onSelect"
        />
      </template>
    </PageHeader>

    <EpisodeBar v-if="store.current" />

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个项目">
      <template #extra>
        <n-button size="small" @click="router.push('/import')">去导入</n-button>
      </template>
    </n-empty>

    <template v-else>
      <!-- Stage controls -->
      <div class="stages">
        <n-card class="stage-card" :bordered="false">
          <div class="stage-head">
            <div>
              <div class="stage-title">阶段一 · 全局分析</div>
              <div class="stage-desc">{{ analyzeDesc }}</div>
            </div>
            <n-popconfirm
              v-if="episodeAnalyzed"
              positive-text="覆盖分析"
              negative-text="取消"
              @positive-click="runAnalyze"
            >
              <template #trigger>
                <n-button type="primary" :loading="analyzing" :disabled="stageBusy">
                  <template #icon><n-icon :component="SparklesOutline" /></template>
                  {{ analyzeButtonText }}
                </n-button>
              </template>
              会覆盖当前集分析结果，并清空本集旧分镜与连续性缓存；其他分集不受影响。
            </n-popconfirm>
            <n-button v-else type="primary" :loading="analyzing" :disabled="stageBusy" @click="runAnalyze">
              <template #icon><n-icon :component="SparklesOutline" /></template>
              {{ analyzeButtonText }}
            </n-button>
          </div>
          <div class="stage-note" :class="{ warn: episodeAnalyzed }">
            <template v-if="episodeAnalyzed">
              当前集已分析：再次执行会替换本集对故事圣经的贡献，并要求重新拆解本集分镜。
            </template>
            <template v-else-if="hasSharedBible">
              续集首次分析：不会清空其他集分镜，只会把本集新增信息接入长线故事圣经。
            </template>
            <template v-else>
              尚未建立故事圣经：请先完成本阶段，再进入分批拆解。
            </template>
          </div>
        </n-card>

        <n-card class="stage-card" :bordered="false">
          <div class="stage-head">
            <div>
              <div class="stage-title">阶段二 · 分批拆解</div>
              <div class="stage-desc">自动＝强制 LLM 按分镜模式拆解；手动＝人工切段、LLM 逐段填结构</div>
            </div>
            <n-popconfirm
              v-if="shots.length"
              positive-text="覆盖拆解"
              negative-text="取消"
              @positive-click="runDecompose"
            >
              <template #trigger>
                <n-button type="primary" :disabled="!canDecompose || stageBusy" :loading="decomposing">
                  <template #icon><n-icon :component="GitBranchOutline" /></template>
                  {{ decomposeMode === 'manual' ? '按手动分镜结构化' : '重新拆解' }}
                </n-button>
              </template>
              会覆盖当前集分镜结构，并清空本集连续性决策、站位图、导演图与尾帧缓存。
            </n-popconfirm>
            <n-button
              v-else
              type="primary"
              :disabled="!canDecompose || stageBusy"
              :loading="decomposing"
              @click="runDecompose"
            >
              <template #icon><n-icon :component="GitBranchOutline" /></template>
              {{ decomposeMode === 'manual' ? '按手动分镜结构化' : '开始拆解' }}
            </n-button>
          </div>
          <div v-if="decomposeDisabledReason" class="stage-note warn">{{ decomposeDisabledReason }}</div>

          <div class="stage-controls">
            <n-radio-group v-model:value="decomposeMode" size="small">
              <n-radio-button value="auto">自动（强制 LLM）</n-radio-button>
              <n-radio-button value="manual">手动分镜</n-radio-button>
            </n-radio-group>
            <n-tooltip v-if="decomposeMode === 'auto'" trigger="hover">
              <template #trigger>
                <n-select
                  v-model:value="selectedPreset"
                  :options="presetOptions"
                  size="small"
                  placeholder="分镜模式（剧本类型）"
                  style="width: 200px"
                />
              </template>
              分镜模式＝不同剧本类型的拆解提示词；可在「提示词模板 · 分批拆解」里新增自定义预设
            </n-tooltip>
          </div>

          <div v-if="decomposeMode === 'manual'" class="manual-box">
            <div class="manual-hint">
              一段（空行分隔）＝一个分镜，由你决定镜头边界；其余结构（场景/角色/动作/机位/对白/时长）由 LLM 逐段填充。
              当前共 <b>{{ manualSegments.length }}</b> 个分镜。
            </div>
            <n-input
              v-model:value="manualText"
              type="textarea"
              :autosize="{ minRows: 6, maxRows: 16 }"
              placeholder="把本集原文按镜切分，用空行分隔每个分镜片段……"
            />
          </div>

          <n-progress
            v-if="decomposing"
            type="line"
            :percentage="progressPct"
            :indicator-placement="'inside'"
            processing
            style="margin-top: 12px"
          />
        </n-card>
      </div>

      <n-alert
        v-if="stageErrorDetail"
        type="error"
        :bordered="false"
        class="stage-error"
        title="阶段任务失败"
      >
        <div class="stage-error-body">
          <span>{{ stageErrorDetail }}</span>
          <n-button size="tiny" quaternary @click="clearStageError">清除提示</n-button>
        </div>
      </n-alert>

      <!-- StoryBible -->
      <n-card v-if="bible" title="故事圣经 (StoryBible)" :bordered="false" class="panel">
        <div class="bible-head">
          <div><span class="bible-k">标题</span>{{ bible.title || '—' }}</div>
          <div><span class="bible-k">梗概</span>{{ bible.logline || '—' }}</div>
          <div class="bible-style">
            <span class="bible-k">风格</span>
            <n-input
              v-model:value="styleDraft"
              size="small"
              placeholder="未设则用 AI 分析结果（可手动覆盖）"
              :loading="savingStyle"
              style="max-width: 420px"
              @blur="saveStyle"
              @keyup.enter="saveStyle"
            />
            <span class="bible-hint">全流程统一沿用此风格值；留空将回退 AI 分析缺省</span>
          </div>
        </div>
        <div class="asset-toolbar">
          <n-input v-model:value="characterQuery" size="small" clearable placeholder="搜索人物（姓名/别名/小传/备注/外形）" style="max-width: 360px" />
          <n-button size="small" quaternary @click="showAllCharacters = !showAllCharacters">
            {{ showAllCharacters ? '收起人物' : `展开全部人物${hiddenCharacterCount ? `（+${hiddenCharacterCount}）` : ''}` }}
          </n-button>
        </div>
        <div class="asset-cols">
          <div class="asset-col">
            <div class="asset-h"><n-icon :component="PersonOutline" /> 人物 <span class="tg">@</span></div>
            <n-collapse accordion>
              <n-collapse-item v-for="g in characterGroups" :key="g.key" :title="`${g.key}（${g.items.length}）`">
                <div v-for="(c, i) in g.items" :key="i" class="asset-item">
                  <div class="asset-item-head">
                    <b>@{{ c.name }}</b>
                    <n-tag v-if="c.alias" size="tiny" :bordered="false">别名</n-tag>
                  </div>
                  <div class="asset-d" v-if="c.bio">{{ c.bio }}</div>
                  <div class="asset-d" v-else>{{ c.appearance || c.note }}</div>
                  <div class="asset-mini" v-if="c.alias">别名：{{ c.alias }}</div>
                  <div class="asset-mini" v-if="c.first_appearance">首次出现：{{ c.first_appearance }}</div>
                  <div class="asset-mini" v-if="c.appearance">外形：{{ c.appearance }}</div>
                  <div class="asset-mini" v-if="c.note && c.note !== c.bio">备注：{{ c.note }}</div>
                </div>
              </n-collapse-item>
            </n-collapse>
          </div>
          <div class="asset-col">
            <div class="asset-h"><n-icon :component="LocationOutline" /> 场景 <span class="tg sc">#</span></div>
            <div v-for="(s, i) in bible.scenes" :key="i" class="asset-item">
              <b>#{{ s.name }}</b><div class="asset-d">{{ s.desc }}</div>
            </div>
          </div>
          <div class="asset-col">
            <div class="asset-h"><n-icon :component="CubeOutline" /> 道具 <span class="tg pr">$</span></div>
            <div v-for="(p, i) in bible.props" :key="i" class="asset-item">
              <b>${{ p.name }}</b><div class="asset-d">{{ p.desc }}</div>
            </div>
          </div>
        </div>
        <n-alert v-if="bible.continuity_constraints?.length" type="info" :bordered="false" style="margin-top: 14px" title="连续性约束">
          <ul class="cons"><li v-for="(c, i) in bible.continuity_constraints" :key="i">{{ c }}</li></ul>
        </n-alert>
      </n-card>

      <!-- Shots -->
      <n-card v-if="shots.length" :title="`分镜表（${shots.length}）`" :bordered="false" class="panel">
        <n-data-table
          :columns="shotColumns"
          :data="shots"
          :max-height="420"
          :row-key="(r) => r.shot_no"
          size="small"
          striped
        />
      </n-card>
    </template>
  </div>
</template>

<style scoped>
.stages {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 18px;
}
@media (max-width: 1000px) { .stages { grid-template-columns: 1fr; } }
.stage-card, .panel {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
}
.panel { margin-bottom: 16px; }
.stage-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.stage-title { font-weight: 700; font-size: 15px; }
.stage-desc { color: var(--app-text-muted); font-size: 12px; margin-top: 4px; }
.stage-note {
  margin-top: 14px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--app-border);
  color: var(--app-text-secondary);
  background: color-mix(in srgb, var(--app-surface-2) 72%, transparent);
  font-size: 12px;
  line-height: 1.55;
}
.stage-note.warn {
  border-color: color-mix(in srgb, #f59e0b 42%, var(--app-border));
  color: color-mix(in srgb, #fbbf24 82%, var(--app-text-secondary));
  background: color-mix(in srgb, #f59e0b 10%, transparent);
}
.stage-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  flex-wrap: wrap;
}
.stage-error {
  margin: -4px 0 18px;
}
.stage-error-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  line-height: 1.6;
}
@media (max-width: 700px) {
  .stage-error-body {
    align-items: flex-start;
    flex-direction: column;
  }
}
.manual-box { margin-top: 12px; }
.manual-hint {
  color: var(--app-text-muted);
  font-size: 12px;
  margin-bottom: 8px;
  line-height: 1.6;
}
.bible-head {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 28px;
  margin-bottom: 16px;
  font-size: 14px;
}
.bible-style { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bible-hint { color: var(--app-text-muted); font-size: 11px; }
.bible-k {
  color: var(--app-text-muted);
  margin-right: 8px;
}
.asset-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.asset-cols {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
@media (max-width: 1000px) { .asset-cols { grid-template-columns: 1fr; } }
.asset-h {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--app-border);
}
.tg { color: var(--app-accent); font-weight: 800; }
.tg.sc { color: var(--app-accent-alt); }
.tg.pr { color: #ffb454; }
.asset-item {
  padding: 8px 10px;
  background: var(--app-bg-soft);
  border-radius: 8px;
  margin-bottom: 8px;
}
.asset-d { color: var(--app-text-muted); font-size: 12px; margin-top: 3px; line-height: 1.5; }
.cons { margin: 0; padding-left: 18px; line-height: 1.8; }
</style>
