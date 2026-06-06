<script setup>
import { onMounted, ref } from 'vue'
import {
  NButton, NCard, NForm, NFormItem, NInput, NInputNumber, NSelect, useMessage,
} from 'naive-ui'
import { api } from '../api'

const message = useMessage()

// Schema-driven: adding a setting = add a field here, no template edits needed.
const SCHEMA = [
  {
    group: '生图默认参数',
    fields: [
      { key: 'image_size', label: '默认尺寸', type: 'select', options: ['1024x1024', '1536x1024', '1024x1536', '2048x2048', '16:9@1080p', '16:9@2K', '16:9@4K', '9:16@1080p', '9:16@2K', '9:16@4K', '1:1@1080p', '1:1@2K', '1:1@4K', '4:3@1080p', '4:3@2K', '4:3@4K', '3:4@1080p', '3:4@2K', '3:4@4K'] },
      { key: 'image_quality', label: '质量', type: 'select', options: ['auto', 'low', 'medium', 'high'] },
      { key: 'image_timeout', label: '超时(秒)', type: 'number', min: 30, max: 1800 },
    ],
  },
  {
    group: '生视频默认参数',
    fields: [
      { key: 'video_aspect_ratio', label: '画幅', type: 'select', options: ['16:9', '9:16', '1:1', '4:3', '3:4'] },
      { key: 'video_resolution', label: '分辨率', type: 'select', options: ['480p', '720p', '1080p', '2K', '4K'] },
      { key: 'video_duration', label: '时长(秒)', type: 'number', min: 1, max: 60 },
      { key: 'video_timeout', label: '超时(秒)', type: 'number', min: 60, max: 1800 },
    ],
  },
  {
    group: '分镜拆解',
    hint: '拆解剧本生成分镜时，按单镜目标时长折算每镜承载的文字量；纯画面/旁白约 8 字/秒，对白约 5 字/秒。对白越多，单镜字数越少，避免对白过载；最终会限制在模型支持的合理范围内。',
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
    hint: '单镜喂给模型的参考图总数上限；超过时按优先级裁剪。JSON/Relay 类视频接口默认使用 Data URL(Base64) 直传，避免公网图床失效导致漏图；只有明确需要公网抓取时再切换到公网 URL。',
    fields: [
      { key: 'max_reference_images', label: '总参考图上限', type: 'number', min: 1, max: 16 },
      {
        key: 'video_reference_transport',
        label: '视频参考图传输',
        type: 'select',
        options: [
          { label: 'Data URL(Base64，默认)', value: 'data_url' },
          { label: '自动(Base64 优先)', value: 'auto' },
          { label: '公网 URL', value: 'public_url' },
        ],
      },
    ],
  },
  {
    group: '资产库',
    hint: '资产库一键生成并发数：同时生成多少张资产参考图。适度并发能提升速度，过高会增加 API 压力或触发限流。',
    fields: [
      { key: 'asset_gen_concurrency', label: '一键生成并发数', type: 'number', min: 1, max: 16 },
    ],
  },
  {
    group: '存储',
    hint: '剪映草稿目录填写后，导入剪映会直接把草稿文件夹复制到该目录，同时仍保留 zip 备份。Windows 默认通常为：C:\\Users\\你的用户名\\AppData\\Local\\JianyingPro\\User Data\\Projects\\com.lveditor.draft',
    fields: [
      { key: 'output_dir', label: '输出目录', type: 'text' },
      { key: 'jianying_draft_dir', label: '剪映草稿目录', type: 'text' },
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
  return field.options.map((o) => (typeof o === 'string' ? { label: o, value: o } : o))
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
