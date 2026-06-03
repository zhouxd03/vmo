<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NCard, NTag, NEmpty, NSpace, NProgress, NSpin,
  NCollapse, NCollapseItem, NDataTable, NAlert, useMessage,
} from 'naive-ui'
import {
  SparklesOutline, GitBranchOutline, PersonOutline, LocationOutline, CubeOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import EpisodeBar from '../components/EpisodeBar.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const analyzing = ref(false)
const decomposing = ref(false)
const jobProgress = ref({ done: 0, total: 0 })
let pollTimer = null

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) {
    await store.select(store.projects[0].id)
  }
})
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

async function onSelect(pid) {
  await store.select(pid)
}

const bible = computed(() => store.current?.story_bible)
const shots = computed(() => store.currentEpisode?.shots || [])

async function runAnalyze() {
  if (!store.current) return
  analyzing.value = true
  try {
    await api.analyzeProject(store.current.id, { episode_id: store.currentEpisodeId })
    await store.reloadCurrent()
    message.success('全局分析完成，已并入小说级故事圣经（全集共享）')
  } catch (e) {
    message.error('全局分析失败: ' + e.message)
  } finally {
    analyzing.value = false
  }
}

async function runDecompose() {
  if (!store.current) return
  decomposing.value = true
  jobProgress.value = { done: 0, total: 0 }
  try {
    const { job_id } = await api.decomposeProject(store.current.id, { episode_id: store.currentEpisodeId })
    pollTimer = setInterval(async () => {
      const job = await api.getJob(job_id)
      jobProgress.value = { done: job.progress, total: job.total }
      if (job.status === 'done') {
        clearInterval(pollTimer); pollTimer = null
        await store.reloadCurrent()
        decomposing.value = false
        message.success(`分批拆解完成，共 ${shots.value.length} 个分镜`)
      } else if (job.status === 'error') {
        clearInterval(pollTimer); pollTimer = null
        decomposing.value = false
        message.error('拆解失败: ' + job.error)
      }
    }, 1000)
  } catch (e) {
    decomposing.value = false
    message.error('拆解失败: ' + e.message)
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

const progressPct = computed(() => {
  const { done, total } = jobProgress.value
  return total ? Math.round((done / total) * 100) : 0
})
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
              <div class="stage-desc">通读全篇 → 故事圣经（角色/场景/道具/风格/连续性约束）</div>
            </div>
            <n-button type="primary" :loading="analyzing" @click="runAnalyze">
              <template #icon><n-icon :component="SparklesOutline" /></template>
              {{ bible ? '重新分析' : '开始全局分析' }}
            </n-button>
          </div>
        </n-card>

        <n-card class="stage-card" :bordered="false">
          <div class="stage-head">
            <div>
              <div class="stage-title">阶段二 · 分批拆解</div>
              <div class="stage-desc">按块切分（滑动窗口带全局锚定+上块接口）→ 结构化分镜</div>
            </div>
            <n-button type="primary" :disabled="!bible" :loading="decomposing" @click="runDecompose">
              <template #icon><n-icon :component="GitBranchOutline" /></template>
              {{ shots.length ? '重新拆解' : '开始拆解' }}
            </n-button>
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

      <!-- StoryBible -->
      <n-card v-if="bible" title="故事圣经 (StoryBible)" :bordered="false" class="panel">
        <div class="bible-head">
          <div><span class="bible-k">标题</span>{{ bible.title || '—' }}</div>
          <div><span class="bible-k">梗概</span>{{ bible.logline || '—' }}</div>
          <div><span class="bible-k">风格</span>{{ bible.style || '—' }}</div>
        </div>
        <div class="asset-cols">
          <div class="asset-col">
            <div class="asset-h"><n-icon :component="PersonOutline" /> 人物 <span class="tg">@</span></div>
            <div v-for="(c, i) in bible.characters" :key="i" class="asset-item">
              <b>@{{ c.name }}</b><div class="asset-d">{{ c.appearance || c.note }}</div>
            </div>
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
.bible-head {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 28px;
  margin-bottom: 16px;
  font-size: 14px;
}
.bible-k {
  color: var(--app-text-muted);
  margin-right: 8px;
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
