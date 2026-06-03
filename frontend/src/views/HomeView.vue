<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NIcon, NTag, NSpin, NEmpty, NProgress, useMessage } from 'naive-ui'
import {
  CloudUploadOutline, DocumentTextOutline, CubeOutline,
  GridSharp, AlbumsOutline, ColorWandOutline, CheckmarkCircle, AlertCircle,
  ChevronForward, ChevronDown, FolderOpenOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useThemeStore } from '../stores/theme'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const message = useMessage()
const themeStore = useThemeStore()
const projectStore = useProjectStore()
const health = ref(null)
const credCount = ref(0)

const stageLabel = { imported: '已导入', analyzed: '已分析', decomposed: '已拆解' }

// project management: list -> drill into episodes + per-episode task progress
const expandedId = ref(null)
const overview = ref(null)
const loadingOverview = ref(false)

const steps = [
  { icon: CloudUploadOutline, title: '导入项目', desc: '导入剧本/小说，自动分集', to: '/import' },
  { icon: DocumentTextOutline, title: '剧本解析', desc: '全局分析 → 分批拆解', to: '/script' },
  { icon: CubeOutline, title: '资产库', desc: '人物/场景/道具 + 参考图', to: '/assets' },
  { icon: GridSharp, title: '批量工作台', desc: '逐镜生图/生视频 + 连续性', to: '/worktable' },
  { icon: AlbumsOutline, title: '批次库', desc: '批次记录 / 产物 / 重跑', to: '/library' },
  { icon: ColorWandOutline, title: '提示词模板', desc: '多套方案，可自定义', to: '/templates' },
]

onMounted(async () => {
  try {
    health.value = await api.health()
    const creds = await api.listCredentials()
    credCount.value = Object.values(creds).reduce((a, list) => a + list.length, 0)
  } catch (e) {
    health.value = { ok: false, error: String(e) }
  }
  await projectStore.refreshList()
})

async function toggleProject(pid) {
  if (expandedId.value === pid) {
    expandedId.value = null
    overview.value = null
    return
  }
  expandedId.value = pid
  overview.value = null
  loadingOverview.value = true
  try {
    overview.value = await api.getProjectOverview(pid)
  } catch (e) {
    message.error('加载项目概览失败: ' + e.message)
    expandedId.value = null
  } finally {
    loadingOverview.value = false
  }
}

async function openEpisode(pid, eid) {
  await projectStore.select(pid)
  if (eid) projectStore.selectEpisode(eid)
  router.push('/worktable')
}

function pct(p) {
  if (!p || !p.total) return 0
  return Math.round((p.done / p.total) * 100)
}
</script>

<template>
  <div>
    <PageHeader
      title="概览"
      subtitle="剧本 → 动漫 的连续性批量创作工具 · 单机运行"
    >
      <template #actions>
        <n-tag v-if="health && health.ok" type="success" round :bordered="false">
          <template #icon><n-icon :component="CheckmarkCircle" /></template>
          后端已连接 · {{ health.version }}
        </n-tag>
        <n-tag v-else type="error" round :bordered="false">
          <template #icon><n-icon :component="AlertCircle" /></template>
          后端未连接
        </n-tag>
      </template>
    </PageHeader>

    <div class="hero glass">
      <div class="hero-text">
        <h2>从一篇剧本，连续、可控地批量生成大量分镜画面与视频</h2>
        <p>
          导入 → 解析 → 资产确认 → 批量生成 全流程向导式推进；跨分镜连续性由记忆层、尾帧/站位图/导演图与 AI 复核共同保障，避免穿帮与割裂。
        </p>
        <div class="hero-meta">
          <span>凭据库已配置 <b>{{ credCount }}</b> 组 API</span>
          <span class="dot">·</span>
          <span>项目 <b>{{ projectStore.projects.length }}</b> 个</span>
          <span class="dot">·</span>
          <span>当前主题 · {{ themeStore.theme.name }}</span>
        </div>
      </div>
    </div>

    <div class="section-row">
      <h3 class="section-title">项目管理</h3>
      <button class="link-btn" @click="router.push('/import')">
        <n-icon :component="CloudUploadOutline" /> 导入新项目
      </button>
    </div>

    <n-empty v-if="!projectStore.projects.length" description="还没有项目，先去导入一部剧本/小说" class="empty">
      <template #extra>
        <button class="link-btn" @click="router.push('/import')">前往导入</button>
      </template>
    </n-empty>

    <div v-else class="proj-list">
      <div v-for="p in projectStore.projects" :key="p.id" class="proj glass">
        <button class="proj-head" @click="toggleProject(p.id)">
          <n-icon class="chev" :component="expandedId === p.id ? ChevronDown : ChevronForward" />
          <n-icon class="folder" :component="FolderOpenOutline" />
          <div class="proj-name">{{ p.name }}</div>
          <div class="proj-meta">
            <n-tag size="small" round :bordered="false" type="success">{{ p.episode_count || 1 }} 集</n-tag>
            <span>{{ p.segment_count }} 片段</span>
            <span v-if="p.shot_count">· {{ p.shot_count }} 分镜</span>
          </div>
        </button>

        <div v-if="expandedId === p.id" class="proj-body">
          <n-spin v-if="loadingOverview" size="small" />
          <div v-else-if="overview" class="ep-list">
            <div v-for="ep in overview.episodes" :key="ep.id" class="ep" @click="openEpisode(p.id, ep.id)">
              <div class="ep-top">
                <span class="ep-name">{{ ep.name }}</span>
                <span class="ep-stage" :data-stage="ep.stage">{{ stageLabel[ep.stage] || ep.stage }}</span>
                <span class="ep-dim">{{ ep.shot_count }} 分镜 · {{ ep.char_count }} 字</span>
              </div>
              <div class="ep-prog">
                <div class="prog-cell">
                  <span class="prog-label">图片</span>
                  <n-progress
                    type="line" :percentage="pct(ep.progress.image)" :height="6"
                    :show-indicator="false"
                    :status="ep.progress.image.error ? 'error' : 'success'"
                  />
                  <span class="prog-num">{{ ep.progress.image.done }}/{{ ep.progress.image.total }}</span>
                </div>
                <div class="prog-cell">
                  <span class="prog-label">视频</span>
                  <n-progress
                    type="line" :percentage="pct(ep.progress.video)" :height="6"
                    :show-indicator="false"
                    :status="ep.progress.video.error ? 'error' : 'success'"
                  />
                  <span class="prog-num">{{ ep.progress.video.done }}/{{ ep.progress.video.total }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <h3 class="section-title workflow-title">工作流</h3>
    <div class="grid">
      <button v-for="s in steps" :key="s.to" class="step glass" @click="router.push(s.to)">
        <div class="step-icon"><n-icon :component="s.icon" size="22" /></div>
        <div class="step-title">{{ s.title }}</div>
        <div class="step-desc">{{ s.desc }}</div>
      </button>
    </div>
  </div>
</template>

<style scoped>
.hero {
  padding: 28px 32px;
  margin-bottom: 24px;
  background:
    linear-gradient(120deg, color-mix(in srgb, var(--app-accent) 12%, transparent), transparent 60%),
    color-mix(in srgb, var(--app-surface) 82%, transparent);
}
.hero-text h2 {
  margin: 0 0 12px;
  font-size: 24px;
  line-height: 1.35;
}
.hero-text p {
  margin: 0;
  max-width: 720px;
  color: var(--app-text-secondary);
  line-height: 1.7;
}
.hero-meta {
  margin-top: 16px;
  color: var(--app-text-muted);
  font-size: 13px;
}
.hero-meta b { color: var(--app-accent); }
.dot { margin: 0 10px; }

.section-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 0 14px;
}
.section-title {
  font-size: 15px;
  margin: 0;
  color: var(--app-text-secondary);
}
.workflow-title { margin: 28px 0 14px; }
.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--app-accent-soft);
  color: var(--app-accent);
  border: none;
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
}
.link-btn:hover { filter: brightness(1.08); }
.empty { padding: 32px 0; }

.proj-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 8px;
}
.proj { overflow: hidden; }
.proj-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--app-text-primary);
  text-align: left;
}
.proj-head:hover { background: var(--app-accent-soft); }
.chev { color: var(--app-text-muted); }
.folder { color: var(--app-accent); }
.proj-name { font-weight: 700; font-size: 15px; }
.proj-meta {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--app-text-muted);
  font-size: 13px;
}
.proj-body {
  padding: 4px 18px 16px;
  border-top: 1px solid var(--app-border);
}
.ep-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}
.ep {
  padding: 12px 14px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--app-surface) 60%, transparent);
  border: 1px solid var(--app-border);
  cursor: pointer;
  transition: border-color 0.15s;
}
.ep:hover { border-color: var(--app-accent); }
.ep-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.ep-name { font-weight: 600; }
.ep-dim { margin-left: auto; color: var(--app-text-muted); font-size: 12px; }
.ep-stage {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--app-border);
  color: var(--app-text-secondary);
}
.ep-stage[data-stage="decomposed"] { background: color-mix(in srgb, var(--app-accent) 22%, transparent); color: var(--app-accent); }
.ep-stage[data-stage="analyzed"] { background: color-mix(in srgb, var(--app-accent-alt) 22%, transparent); color: var(--app-accent-alt); }
.ep-prog {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.prog-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.prog-label { font-size: 12px; color: var(--app-text-muted); width: 28px; }
.prog-cell :deep(.n-progress) { flex: 1; }
.prog-num { font-size: 12px; color: var(--app-text-secondary); min-width: 44px; text-align: right; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}
.step {
  text-align: left;
  padding: 20px;
  cursor: pointer;
  color: var(--app-text-primary);
  transition: transform 0.15s, border-color 0.15s;
}
.step:hover {
  transform: translateY(-3px);
  border-color: var(--app-accent);
}
.step-icon {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--app-accent-soft);
  color: var(--app-accent);
  margin-bottom: 14px;
}
.step-title {
  font-weight: 700;
  margin-bottom: 6px;
}
.step-desc {
  color: var(--app-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
</style>
