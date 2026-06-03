<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NUpload, NUploadDragger, NIcon, NText, NInput, NButton, NSpace, NCard,
  NRadioGroup, NRadioButton, NTag, NEmpty, useMessage,
} from 'naive-ui'
import { CloudUploadOutline, DocumentTextOutline, ArrowForwardOutline, TrashOutline } from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { useProjectStore } from '../stores/project'
import { onMounted } from 'vue'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const fileName = ref('')
const fileType = ref('txt')
const projectName = ref('')
const rawText = ref('')
const importing = ref(false)

onMounted(() => store.refreshList())

function detectType(name) {
  if (name.toLowerCase().endsWith('.srt')) return 'srt'
  return 'txt'
}

function onFile({ file }) {
  const raw = file.file
  if (!raw) return false
  fileName.value = raw.name
  fileType.value = detectType(raw.name)
  if (!projectName.value) projectName.value = raw.name.replace(/\.(srt|txt)$/i, '')
  const reader = new FileReader()
  reader.onload = (e) => { rawText.value = e.target.result || '' }
  reader.readAsText(raw, 'utf-8')
  return false // prevent auto-upload
}

async function doImport() {
  if (!rawText.value.trim()) {
    message.warning('请先选择文件或粘贴文本')
    return
  }
  importing.value = true
  try {
    const project = await store.importProject({
      name: projectName.value,
      file_type: fileType.value,
      text: rawText.value,
    })
    message.success(`已导入「${project.name}」，解析出 ${project.segments.length} 个片段`)
    router.push('/script')
  } catch (e) {
    message.error('导入失败: ' + e.message)
  } finally {
    importing.value = false
  }
}

async function openProject(pid) {
  await store.select(pid)
  router.push('/script')
}

async function removeProject(pid) {
  await store.remove(pid)
  message.success('已删除项目')
}
</script>

<template>
  <div>
    <PageHeader title="项目导入" subtitle="导入 SRT 音轨稿或 TXT 文稿，作为批量创作的前置项目" />

    <div class="import-grid">
      <n-card title="新建导入" :bordered="false" class="panel">
        <n-space vertical size="large">
          <n-upload :show-file-list="false" :default-upload="false" accept=".srt,.txt" @change="onFile">
            <n-upload-dragger>
              <div style="padding: 8px">
                <n-icon size="40" :component="CloudUploadOutline" :depth="3" />
                <div style="margin-top: 10px">
                  <n-text style="font-size: 15px">点击或拖拽文件到此处</n-text>
                </div>
                <n-text depth="3" style="font-size: 12px">支持 .srt（带时间轴+序号）与 .txt（剧本/分镜稿）</n-text>
              </div>
            </n-upload-dragger>
          </n-upload>

          <div v-if="fileName" class="picked">
            <n-icon :component="DocumentTextOutline" />
            <span>{{ fileName }}</span>
            <n-tag size="small" round :bordered="false" type="info">{{ rawText.length }} 字</n-tag>
          </div>

          <div>
            <div class="field-label">项目名称</div>
            <n-input v-model:value="projectName" placeholder="给这个项目起个名字" />
          </div>

          <div>
            <div class="field-label">文件类型</div>
            <n-radio-group v-model:value="fileType">
              <n-radio-button value="txt">TXT 文稿</n-radio-button>
              <n-radio-button value="srt">SRT 音轨稿</n-radio-button>
            </n-radio-group>
          </div>

          <div>
            <div class="field-label">内容预览 / 可直接粘贴</div>
            <n-input
              v-model:value="rawText"
              type="textarea"
              :autosize="{ minRows: 5, maxRows: 12 }"
              placeholder="可直接粘贴剧本文本，无需文件"
            />
          </div>

          <n-button type="primary" size="large" :loading="importing" @click="doImport">
            <template #icon><n-icon :component="ArrowForwardOutline" /></template>
            导入并进入剧本解析
          </n-button>
        </n-space>
      </n-card>

      <n-card title="已有项目" :bordered="false" class="panel">
        <n-empty v-if="!store.projects.length" description="还没有项目" />
        <div v-else class="proj-list">
          <div v-for="p in store.projects" :key="p.id" class="proj" @click="openProject(p.id)">
            <div class="proj-main">
              <div class="proj-name">{{ p.name }}</div>
              <div class="proj-meta">
                <n-tag size="small" round :bordered="false" type="success">{{ p.episode_count || 1 }} 集</n-tag>
                <span>{{ p.segment_count }} 片段</span>
                <span v-if="p.shot_count">· {{ p.shot_count }} 分镜</span>
              </div>
            </div>
            <n-button quaternary circle size="small" @click.stop="removeProject(p.id)">
              <template #icon><n-icon :component="TrashOutline" /></template>
            </n-button>
          </div>
        </div>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
.import-grid {
  display: grid;
  grid-template-columns: 1.3fr 1fr;
  gap: 18px;
}
@media (max-width: 1100px) {
  .import-grid { grid-template-columns: 1fr; }
}
.panel {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
}
.field-label {
  font-size: 13px;
  color: var(--app-text-secondary);
  margin-bottom: 8px;
}
.picked {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--app-text-secondary);
  font-size: 13px;
}
.proj-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.proj {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 14px;
  background: var(--app-bg-soft);
  border: 1px solid var(--app-border);
  border-radius: 12px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.proj:hover { border-color: var(--app-accent); }
.proj-name { font-weight: 600; margin-bottom: 5px; }
.proj-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--app-text-muted);
  font-size: 12px;
}
</style>
