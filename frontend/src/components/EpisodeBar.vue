<script setup>
import { computed, ref } from 'vue'
import {
  NButton, NIcon, NModal, NCard, NInput, NUpload, NUploadDragger, NSpace,
  NText, NPopconfirm, NTooltip, useMessage,
} from 'naive-ui'
import {
  AddOutline, CreateOutline, TrashOutline, ChevronBackOutline,
  ChevronForwardOutline, DocumentTextOutline,
} from '@vicons/ionicons5'
import { api } from '../api'
import { useProjectStore } from '../stores/project'
import { useTasksStore } from '../stores/tasks'

const store = useProjectStore()
const tasks = useTasksStore()
const message = useMessage()
const emit = defineEmits(['open-errors'])

const episodes = computed(() => store.episodes || [])
const activeId = computed(() => store.currentEpisodeId)

const stageLabel = { imported: '已导入', analyzed: '已分析', decomposed: '已拆解' }

// per-episode generation badge (concurrent multi-episode runs each show here)
const genLabel = { running: '生成中', queued: '排队', error: '部分失败', done: '已完成' }
function genStat(ep) { return tasks.episodeStat(ep.id) }
function shotCount(ep) { return ep.shot_count ?? (ep.shots ? ep.shots.length : 0) }
function openErrors(ep, ev) {
  ev?.stopPropagation()
  if (genStat(ep)?.status !== 'error') return
  emit('open-errors', ep.id)
}

// ── add episode modal ──
const showAdd = ref(false)
const addName = ref('')
const addText = ref('')
const addFileType = ref('txt')
const adding = ref(false)

function openAdd() {
  addName.value = `第${(episodes.value.at(-1)?.idx || episodes.value.length) + 1}集`
  addText.value = ''
  addFileType.value = 'txt'
  showAdd.value = true
}

function onFile({ file }) {
  const f = file.file
  if (!f) return
  addFileType.value = f.name.toLowerCase().endsWith('.srt') ? 'srt' : 'txt'
  const reader = new FileReader()
  reader.onload = (e) => { addText.value = e.target.result }
  reader.readAsText(f)
  return false
}

async function submitAdd() {
  if (!addText.value.trim()) { message.warning('请粘贴或上传该集剧本文本'); return }
  adding.value = true
  try {
    await store.addEpisode({ name: addName.value, text: addText.value, file_type: addFileType.value })
    message.success('已新增分集')
    showAdd.value = false
  } catch (e) {
    message.error('新增失败: ' + e.message)
  } finally {
    adding.value = false
  }
}

// ── rename ──
const showRename = ref(false)
const renameId = ref('')
const renameName = ref('')
function openRename(ep) {
  renameId.value = ep.id
  renameName.value = ep.name
  showRename.value = true
}
async function submitRename() {
  try {
    await store.renameEpisode(renameId.value, renameName.value)
    showRename.value = false
  } catch (e) { message.error('重命名失败: ' + e.message) }
}

async function removeEp(ep) {
  if (episodes.value.length <= 1) { message.warning('至少保留一集'); return }
  try {
    await store.removeEpisode(ep.id)
    message.success('已删除分集')
  } catch (e) { message.error('删除失败: ' + e.message) }
}

async function move(ep, dir) {
  const ids = episodes.value.map((e) => e.id)
  const i = ids.indexOf(ep.id)
  const j = i + dir
  if (j < 0 || j >= ids.length) return
  ;[ids[i], ids[j]] = [ids[j], ids[i]]
  try {
    await api.reorderEpisodes(store.currentId, ids)
    await store.reloadCurrent()
  } catch (e) { message.error('重排失败: ' + e.message) }
}
</script>

<template>
  <div class="ep-bar">
    <div class="ep-tabs">
      <div
        v-for="(ep, i) in episodes"
        :key="ep.id"
        class="ep-tab"
        :class="{ active: ep.id === activeId }"
        @click="store.selectEpisode(ep.id)"
      >
        <span class="ep-name">{{ ep.name }}</span>
        <span class="ep-stage" :data-stage="ep.stage">{{ stageLabel[ep.stage] || ep.stage }}</span>
        <span class="ep-meta">{{ shotCount(ep) }}镜</span>
        <span
          v-if="genStat(ep)"
          class="ep-gen"
          :class="{ clickable: genStat(ep).status === 'error' }"
          :data-gen="genStat(ep).status"
          :title="genStat(ep).status === 'error' ? '查看本集全部错误' : ''"
          @click="openErrors(ep, $event)"
        >
          <span v-if="genStat(ep).status === 'running'" class="dot" />
          {{ genLabel[genStat(ep).status] }} {{ genStat(ep).done }}/{{ genStat(ep).total }}
        </span>
        <span class="ep-ops" v-if="ep.id === activeId">
          <n-tooltip><template #trigger>
            <n-icon class="op" :component="ChevronBackOutline" @click.stop="move(ep, -1)" /></template>前移</n-tooltip>
          <n-tooltip><template #trigger>
            <n-icon class="op" :component="ChevronForwardOutline" @click.stop="move(ep, 1)" /></template>后移</n-tooltip>
          <n-tooltip><template #trigger>
            <n-icon class="op" :component="CreateOutline" @click.stop="openRename(ep)" /></template>重命名</n-tooltip>
          <n-popconfirm @positive-click="removeEp(ep)">
            <template #trigger><n-icon class="op danger" :component="TrashOutline" @click.stop /></template>
            删除「{{ ep.name }}」？该集分镜将一并删除（资产库/故事圣经为小说级共享，不受影响）。
          </n-popconfirm>
        </span>
      </div>
    </div>
    <n-button quaternary size="small" class="ep-add" @click="openAdd">
      <template #icon><n-icon :component="AddOutline" /></template>新建集
    </n-button>

    <!-- add modal -->
    <n-modal v-model:show="showAdd">
      <n-card style="width: 600px" title="新增分集" :bordered="false" role="dialog">
        <n-space vertical size="large">
          <n-input v-model:value="addName" placeholder="分集名称，如 第2集" />
          <n-upload :default-upload="false" :show-file-list="false" @change="onFile">
            <n-upload-dragger>
              <div style="padding: 8px">
                <n-icon size="28" :component="DocumentTextOutline" />
                <div style="margin-top: 6px">点击上传 .txt / .srt，或直接在下方粘贴</div>
              </div>
            </n-upload-dragger>
          </n-upload>
          <n-input
            v-model:value="addText"
            type="textarea"
            placeholder="粘贴该集剧本文本…"
            :autosize="{ minRows: 6, maxRows: 12 }"
          />
          <n-text depth="3" style="font-size: 12px">
            资产库（@人物/#场景/$道具）与故事圣经是【小说级】共享，新集会自动复用，跨集承接。
          </n-text>
        </n-space>
        <template #footer>
          <n-space justify="end">
            <n-button @click="showAdd = false">取消</n-button>
            <n-button type="primary" :loading="adding" @click="submitAdd">创建分集</n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>

    <!-- rename modal -->
    <n-modal v-model:show="showRename">
      <n-card style="width: 400px" title="重命名分集" :bordered="false" role="dialog">
        <n-input v-model:value="renameName" @keyup.enter="submitRename" />
        <template #footer>
          <n-space justify="end">
            <n-button @click="showRename = false">取消</n-button>
            <n-button type="primary" @click="submitRename">保存</n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>
  </div>
</template>

<style scoped>
.ep-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0 14px;
  flex-wrap: wrap;
}
.ep-tabs { display: flex; gap: 8px; flex-wrap: wrap; }
.ep-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-radius: 10px;
  background: var(--app-bg-soft);
  border: 1px solid var(--app-border);
  cursor: pointer;
  transition: all .15s;
  user-select: none;
}
.ep-tab:hover { border-color: color-mix(in srgb, var(--app-accent) 50%, transparent); }
.ep-tab.active {
  border-color: var(--app-accent);
  background: color-mix(in srgb, var(--app-accent) 12%, var(--app-bg-soft));
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--app-accent) 40%, transparent) inset;
}
.ep-name { font-weight: 700; font-size: 13px; }
.ep-stage {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--app-text-muted) 22%, transparent);
  color: var(--app-text-muted);
}
.ep-stage[data-stage="decomposed"] { background: color-mix(in srgb, var(--app-accent) 22%, transparent); color: var(--app-accent); }
.ep-stage[data-stage="analyzed"] { background: color-mix(in srgb, var(--app-accent-alt) 22%, transparent); color: var(--app-accent-alt); }
.ep-meta { font-size: 11px; color: var(--app-text-muted); }
.ep-gen {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; padding: 1px 7px; border-radius: 6px;
  background: color-mix(in srgb, var(--app-text-muted) 18%, transparent);
  color: var(--app-text-muted);
}
.ep-gen[data-gen="running"] { background: color-mix(in srgb, var(--app-accent) 20%, transparent); color: var(--app-accent); }
.ep-gen[data-gen="queued"] { background: color-mix(in srgb, var(--app-accent-alt) 18%, transparent); color: var(--app-accent-alt); }
.ep-gen[data-gen="done"] { background: color-mix(in srgb, #22c55e 22%, transparent); color: #22c55e; }
.ep-gen[data-gen="error"] { background: color-mix(in srgb, #ef4444 22%, transparent); color: #ef4444; }
.ep-gen.clickable { cursor: pointer; }
.ep-gen.clickable:hover { box-shadow: 0 0 0 1px currentColor inset; }
.ep-gen .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; animation: epPulse 1s infinite; }
@keyframes epPulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }
.ep-ops { display: flex; align-items: center; gap: 7px; margin-left: 4px; padding-left: 8px; border-left: 1px solid var(--app-border); }
.op { font-size: 15px; color: var(--app-text-muted); cursor: pointer; }
.op:hover { color: var(--app-accent); }
.op.danger:hover { color: #ff6b6b; }
</style>
