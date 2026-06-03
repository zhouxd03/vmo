<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NIcon, NTag } from 'naive-ui'
import {
  CloudUploadOutline, DocumentTextOutline, CubeOutline,
  GridSharp, AlbumsOutline, ColorWandOutline, CheckmarkCircle, AlertCircle,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useThemeStore } from '../stores/theme'

const router = useRouter()
const themeStore = useThemeStore()
const health = ref(null)
const credCount = ref(0)

const steps = [
  { icon: CloudUploadOutline, title: '1 · 导入项目', desc: '导入 SRT 音轨稿 / TXT 文稿', to: '/import' },
  { icon: DocumentTextOutline, title: '2 · 剧本解析', desc: '两阶段 LLM：全局分析 → 分批拆解', to: '/script' },
  { icon: CubeOutline, title: '3 · 资产库', desc: '人物@ / 场景# / 道具$ 提取与参考图', to: '/assets' },
  { icon: GridSharp, title: '4 · 批量工作台', desc: '逐镜总览：生图/生视频 + @#$ 引用 + 锁定 + 连续性', to: '/worktable' },
  { icon: AlbumsOutline, title: '5 · 批次库', desc: '批次记录 / 产物预览 / 重跑 / 删除', to: '/library' },
  { icon: ColorWandOutline, title: '6 · 提示词模板', desc: '多套备选方案，可自定义新增/切换', to: '/templates' },
]

onMounted(async () => {
  try {
    health.value = await api.health()
    const creds = await api.listCredentials()
    credCount.value = Object.values(creds).reduce((a, list) => a + list.length, 0)
  } catch (e) {
    health.value = { ok: false, error: String(e) }
  }
})
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
          <span>当前主题 · {{ themeStore.theme.name }}</span>
        </div>
      </div>
    </div>

    <h3 class="section-title">工作流</h3>
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
  margin-bottom: 28px;
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
.hero-meta b {
  color: var(--app-accent);
}
.dot {
  margin: 0 10px;
}
.section-title {
  font-size: 15px;
  margin: 0 0 14px;
  color: var(--app-text-secondary);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
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
