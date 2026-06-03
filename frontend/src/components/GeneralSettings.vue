<script setup>
import { onMounted, ref } from 'vue'
import {
  NCard, NForm, NFormItem, NInputNumber, NSelect, NInput, NButton, NSpace, useMessage,
} from 'naive-ui'
import { api } from '../api'

const message = useMessage()

// Schema-driven: adding a setting = add a field here, no template edits needed.
const SCHEMA = [
  {
    group: '生图默认参数',
    fields: [
      { key: 'image_size', label: '默认尺寸', type: 'select', options: ['1024x1024', '1536x1024', '1024x1536', '2048x2048'] },
      { key: 'image_quality', label: '质量', type: 'select', options: ['auto', 'low', 'medium', 'high'] },
      { key: 'image_timeout', label: '超时(秒)', type: 'number', min: 30, max: 600 },
    ],
  },
  {
    group: '生视频默认参数',
    fields: [
      { key: 'video_aspect_ratio', label: '画幅', type: 'select', options: ['16:9', '9:16', '1:1', '4:3'] },
      { key: 'video_resolution', label: '分辨率', type: 'select', options: ['480p', '720p', '1080p'] },
      { key: 'video_duration', label: '时长(秒)', type: 'number', min: 1, max: 60 },
      { key: 'video_timeout', label: '超时(秒)', type: 'number', min: 60, max: 1800 },
    ],
  },
  {
    group: '分镜拆解',
    hint: '拆解剧本生成分镜时按「单镜目标时长」折算每镜承载文字：纯画面/旁白≈8字/秒（如15秒≈120字），对白≈5字/秒；对白越多单镜字数越少，避免对话过载。单镜估算时长以此为中心，最终钳制到 3–15 秒。',
    fields: [
      { key: 'shot_target_seconds', label: '单镜目标时长(秒)', type: 'number', min: 3, max: 15 },
    ],
  },
  {
    group: '批量引擎',
    fields: [
      { key: 'max_concurrency', label: '最大并发', type: 'number', min: 1, max: 16 },
      { key: 'max_retries', label: '失败重试次数', type: 'number', min: 0, max: 10 },
      { key: 'task_interval_ms', label: '任务间隔(ms)', type: 'number', min: 0, max: 10000 },
      { key: 'max_parallel_episodes', label: '并发分集数', type: 'number', min: 1, max: 8 },
    ],
  },
  {
    group: '参考图引用',
    hint: '单镜喂给模型的垫图总数上限；超出时按优先级裁剪：导演图/首帧图 ＞ 角色图 ＞ 背景图 ＞ 配角图 ＞ 道具图。',
    fields: [
      { key: 'max_reference_images', label: '总参考图上限', type: 'number', min: 1, max: 16 },
    ],
  },
  {
    group: '存储',
    fields: [
      { key: 'output_dir', label: '输出目录', type: 'text' },
    ],
  },
]

const settings = ref({})
const saving = ref(false)

async function reload() {
  settings.value = await api.getSettings()
}
onMounted(reload)

async function save() {
  saving.value = true
  try {
    await api.saveSettings(settings.value)
    message.success('设置已保存')
  } catch (e) {
    message.error('保存失败: ' + e.message)
  } finally {
    saving.value = false
  }
}

function selectOptions(field) {
  return field.options.map((o) => ({ label: o, value: o }))
}
</script>

<template>
  <div class="settings">
    <n-card v-for="grp in SCHEMA" :key="grp.group" class="grp-card" :title="grp.group" :bordered="false">
      <div v-if="grp.hint" class="grp-hint">{{ grp.hint }}</div>
      <n-form label-placement="left" label-width="120" :show-feedback="false">
        <n-form-item v-for="f in grp.fields" :key="f.key" :label="f.label">
          <n-select v-if="f.type === 'select'" v-model:value="settings[f.key]" :options="selectOptions(f)" style="max-width: 280px" />
          <n-input-number v-else-if="f.type === 'number'" v-model:value="settings[f.key]" :min="f.min" :max="f.max" style="max-width: 200px" />
          <n-input v-else v-model:value="settings[f.key]" style="max-width: 420px" />
        </n-form-item>
      </n-form>
    </n-card>
    <div class="save-row">
      <n-button type="primary" :loading="saving" @click="save">保存设置</n-button>
    </div>
  </div>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.grp-card {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border);
  border-radius: var(--r-card);
}
.grp-card :deep(.n-form-item) {
  margin-bottom: 14px;
}
.grp-hint {
  font-size: 12px;
  color: var(--app-text-muted);
  margin: -4px 0 12px;
  line-height: 1.5;
}
.save-row {
  display: flex;
  justify-content: flex-end;
}
</style>
