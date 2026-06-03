<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NCard, NEmpty, NModal, NForm, NFormItem, NInput,
  NImage, NTag, NSpin, NPopconfirm, NAlert, useMessage,
} from 'naive-ui'
import {
  PersonOutline, LocationOutline, CubeOutline, AddOutline, ImageOutline,
  CreateOutline, TrashOutline, DownloadOutline, SparklesOutline, FlaskOutline,
  FlashOutline, CloudUploadOutline, RefreshOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const assets = ref([])
const loading = ref(false)

const COLS = [
  { type: 'character', label: '人物', trigger: '@', icon: PersonOutline, cls: 'c' },
  { type: 'scene', label: '场景', trigger: '#', icon: LocationOutline, cls: 's' },
  { type: 'prop', label: '道具', trigger: '$', icon: CubeOutline, cls: 'p' },
]

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  if (store.current) await loadAssets()
})

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.stage}）`, value: p.id })))

async function onSelect(pid) {
  await store.select(pid)
  await loadAssets()
}

async function loadAssets() {
  if (!store.current) return
  loading.value = true
  try { assets.value = await api.listAssets(store.current.id) }
  finally { loading.value = false }
}

const byType = (t) => assets.value.filter((a) => a.type === t)

// ── add / edit modal ──
const modal = ref(false)
const editing = ref(null)
const form = ref({ type: 'character', name: '', desc: '', appearance: '' })

function openAdd(type) {
  editing.value = null
  form.value = { type, name: '', desc: '', appearance: '' }
  modal.value = true
}
function openEdit(a) {
  editing.value = a
  form.value = { type: a.type, name: a.name, desc: a.desc, appearance: a.appearance }
  modal.value = true
}
async function submit() {
  if (!form.value.name.trim()) { message.warning('请填写名称'); return }
  try {
    if (editing.value) {
      await api.updateAsset(store.current.id, editing.value.id, {
        name: form.value.name, desc: form.value.desc, appearance: form.value.appearance,
      })
      message.success('已保存')
    } else {
      await api.addAsset(store.current.id, form.value)
      message.success('已添加资产')
    }
    modal.value = false
    await loadAssets()
  } catch (e) { message.error(e.message) }
}
async function removeAsset(a) {
  await api.deleteAsset(store.current.id, a.id)
  await loadAssets()
}

async function seedFromBible() {
  try {
    await api.seedAssets(store.current.id)
    message.success('已从故事圣经导入资产')
    await loadAssets()
  } catch (e) { message.error(e.message) }
}

// ── one-click generate missing reference images ──
const missingCount = computed(() => assets.value.filter((a) => !a.ref_image).length)
const batchGen = ref({ running: false, done: 0, total: 0, failed: [] })

async function genMissing() {
  if (!store.current) return
  if (!missingCount.value) { message.info('所有资产都已有参考图'); return }
  batchGen.value = { running: true, done: 0, total: missingCount.value, failed: [] }
  try {
    const r = await api.genMissingAssets(store.current.id, {})
    batchGen.value.done = r.generated
    batchGen.value.total = r.total
    batchGen.value.failed = r.failed || []
    await loadAssets()
    if (r.failed?.length) {
      message.warning(`生成完成：成功 ${r.generated}/${r.total}，失败 ${r.failed.length}（可重试）`)
    } else if (r.total) {
      message.success(`已补全 ${r.generated} 张缺失参考图`)
    }
  } catch (e) {
    message.error('一键生成失败: ' + e.message)
  } finally {
    batchGen.value.running = false
  }
}

async function retryFailed() {
  // failed assets still have no ref_image, so generate-missing only retries them.
  await genMissing()
}

// ── import an external image as the reference ──
const importingId = ref(null)
const fileInput = ref(null)
let importTarget = null
function pickImport(a) {
  importTarget = a
  fileInput.value?.click()
}
async function onFilePicked(e) {
  const file = e.target.files?.[0]
  e.target.value = ''  // allow re-picking the same file
  if (!file || !importTarget) return
  importingId.value = importTarget.id
  try {
    await api.importAssetImage(store.current.id, importTarget.id, file)
    message.success(`已导入 ${importTarget.trigger}${importTarget.name} 参考图`)
    await loadAssets()
  } catch (err) {
    message.error('导入失败: ' + err.message)
  } finally {
    importingId.value = null
    importTarget = null
  }
}

const genningId = ref(null)
async function genRef(a) {
  genningId.value = a.id
  try {
    await api.genAssetRefImage(store.current.id, a.id, {})
    message.success(`已生成 ${a.trigger}${a.name} 参考图`)
    await loadAssets()
  } catch (e) {
    message.error('生成失败: ' + e.message)
  } finally { genningId.value = null }
}

function imgUrl(a) {
  return a.ref_image ? api.assetImageUrl(store.current.id, a.ref_image) : null
}

// ── @ resolve tester ──
const testText = ref('')
const resolved = ref(null)
async function runResolve() {
  if (!store.current) return
  resolved.value = await api.resolveMentions(store.current.id, testText.value)
}
</script>

<template>
  <div>
    <PageHeader title="资产库" subtitle="人物 @ / 场景 # / 道具 $ 提取 → 参考图 → 生成时自动 @ 引用">
      <template #actions>
        <n-select
          :value="store.currentId" :options="projectOptions"
          placeholder="选择项目" style="width: 280px" @update:value="onSelect"
        />
      </template>
    </PageHeader>

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个项目">
      <template #extra><n-button size="small" @click="router.push('/import')">去导入</n-button></template>
    </n-empty>

    <template v-else>
      <div class="toolbar">
        <n-button size="small" secondary @click="seedFromBible">
          <template #icon><n-icon :component="SparklesOutline" /></template>
          从故事圣经导入
        </n-button>
        <n-button
          size="small" type="primary"
          :loading="batchGen.running"
          :disabled="!missingCount && !batchGen.running"
          @click="genMissing"
        >
          <template #icon><n-icon :component="FlashOutline" /></template>
          一键生成缺失参考图<span v-if="missingCount">（{{ missingCount }}）</span>
        </n-button>
        <span class="hint">提示：仅补全未生成的资产（后续集新增资产也只补缺）；可逐个「导入」外部图片。</span>
      </div>

      <n-alert
        v-if="batchGen.running || batchGen.total"
        :type="batchGen.failed.length ? 'warning' : (batchGen.running ? 'info' : 'success')"
        :bordered="false" style="margin-bottom: 14px"
      >
        <template v-if="batchGen.running">
          正在生成缺失参考图… 共 {{ batchGen.total }} 个待补全
        </template>
        <template v-else>
          本次补全：成功 {{ batchGen.done }} / {{ batchGen.total }}
          <template v-if="batchGen.failed.length">
            ，失败 {{ batchGen.failed.length }}：
            <n-tag
              v-for="f in batchGen.failed" :key="f.id" size="small"
              type="error" :bordered="false" style="margin: 0 4px 4px 0"
            >{{ f.trigger }}{{ f.name }}</n-tag>
            <n-button size="tiny" tertiary style="margin-left: 6px" @click="retryFailed">
              <template #icon><n-icon :component="RefreshOutline" /></template>重试失败项
            </n-button>
          </template>
        </template>
      </n-alert>
      <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="onFilePicked" />

      <n-spin :show="loading">
        <div class="cols">
          <n-card v-for="col in COLS" :key="col.type" :bordered="false" class="col">
            <div class="col-head">
              <div class="col-title">
                <n-icon :component="col.icon" />
                {{ col.label }} <span class="tg" :class="col.cls">{{ col.trigger }}</span>
                <span class="cnt">{{ byType(col.type).length }}</span>
              </div>
              <n-button size="tiny" tertiary @click="openAdd(col.type)">
                <template #icon><n-icon :component="AddOutline" /></template>添加
              </n-button>
            </div>

            <n-empty v-if="!byType(col.type).length" description="暂无" size="small" style="margin: 24px 0" />
            <div v-else class="cards">
              <div v-for="a in byType(col.type)" :key="a.id" class="asset">
                <div class="thumb">
                  <n-image v-if="imgUrl(a)" :src="imgUrl(a)" object-fit="cover" width="100%" />
                  <div v-else class="thumb-empty"><n-icon :component="ImageOutline" size="22" /></div>
                </div>
                <div class="asset-body">
                  <div class="asset-name"><span class="tg" :class="col.cls">{{ col.trigger }}</span>{{ a.name }}</div>
                  <div class="asset-desc">{{ a.appearance || a.desc || '—' }}</div>
                  <div class="asset-actions">
                    <n-button size="tiny" :loading="genningId === a.id" @click="genRef(a)">
                      <template #icon><n-icon :component="ImageOutline" /></template>
                      {{ a.ref_image ? '重生' : '参考图' }}
                    </n-button>
                    <n-button size="tiny" quaternary :loading="importingId === a.id" title="从外部导入图片" @click="pickImport(a)">
                      <template #icon><n-icon :component="CloudUploadOutline" /></template>
                    </n-button>
                    <n-button size="tiny" quaternary @click="openEdit(a)">
                      <template #icon><n-icon :component="CreateOutline" /></template>
                    </n-button>
                    <n-popconfirm @positive-click="removeAsset(a)">
                      <template #trigger>
                        <n-button size="tiny" quaternary type="error">
                          <template #icon><n-icon :component="TrashOutline" /></template>
                        </n-button>
                      </template>
                      删除资产「{{ a.name }}」？
                    </n-popconfirm>
                  </div>
                </div>
              </div>
            </div>
          </n-card>
        </div>
      </n-spin>

      <!-- @ auto-reference tester -->
      <n-card title="@ 自动引用测试" :bordered="false" class="resolver">
        <div class="resolver-desc">
          输入一段分镜文本，系统会自动识别其中出现的资产名 → 注入参考图占位符
          <code>@image1 / @image2…</code>（缺参考图会告警跳过）。这正是批量生成时喂给模型的逻辑。
        </div>
        <n-input
          v-model:value="testText" type="textarea"
          :autosize="{ minRows: 3, maxRows: 6 }"
          placeholder="例如：林夏走进废弃停车场，手里攥着青铜酒爵……"
        />
        <n-button type="primary" size="small" style="margin-top: 10px" @click="runResolve">
          <template #icon><n-icon :component="FlaskOutline" /></template>解析引用
        </n-button>

        <div v-if="resolved" class="resolved">
          <div class="resolved-row">
            <span class="rk">替换后文本</span>
            <div class="rv mono">{{ resolved.text || '（空）' }}</div>
          </div>
          <div class="resolved-row">
            <span class="rk">引用资产</span>
            <div class="rv">
              <n-tag v-for="m in resolved.materials" :key="m.asset_id" size="small" round
                :type="m.ref_image ? 'success' : 'warning'" :bordered="false" style="margin: 0 6px 6px 0">
                {{ m.placeholder }} = {{ m.trigger }}{{ m.name }}{{ m.ref_image ? '' : '（无参考图）' }}
              </n-tag>
              <span v-if="!resolved.materials.length" class="muted">未识别到任何资产</span>
            </div>
          </div>
          <n-alert v-if="resolved.warnings?.length" type="warning" :bordered="false" style="margin-top: 8px">
            <div v-for="(w, i) in resolved.warnings" :key="i">{{ w }}</div>
          </n-alert>
        </div>
      </n-card>
    </template>

    <!-- add/edit modal -->
    <n-modal v-model:show="modal" preset="card" :title="editing ? '编辑资产' : '添加资产'" style="width: 520px">
      <n-form label-placement="top">
        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="资产名（生成时用作 @/#/$ 引用名）" />
        </n-form-item>
        <n-form-item v-if="form.type === 'character'" label="外形锚定（appearance）">
          <n-input v-model:value="form.appearance" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
            placeholder="外形/服装/发型/特征，用于生成三视图参考图" />
        </n-form-item>
        <n-form-item :label="form.type === 'prop' ? '形制/年代/材质（防穿帮）' : '描述'">
          <n-input v-model:value="form.desc" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
            :placeholder="form.type === 'prop' ? '如：古代青铜酒爵，非现代玻璃高脚杯' : '地点/时代/光影/布局 等'" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 10px">
          <n-button @click="modal = false">取消</n-button>
          <n-button type="primary" @click="submit">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex; align-items: center; gap: 14px; margin-bottom: 16px;
}
.hint { color: var(--app-text-muted); font-size: 12px; }
.cols {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
}
@media (max-width: 1100px) { .cols { grid-template-columns: 1fr; } }
.col {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card);
}
.col-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--app-border);
}
.col-title { display: flex; align-items: center; gap: 7px; font-weight: 700; }
.cnt {
  background: var(--app-bg-soft); border-radius: 10px; padding: 0 8px;
  font-size: 12px; color: var(--app-text-muted);
}
.tg { font-weight: 800; }
.tg.c { color: var(--app-accent); }
.tg.s { color: var(--app-accent-alt); }
.tg.p { color: #ffb454; }
.cards { display: flex; flex-direction: column; gap: 10px; }
.asset {
  display: flex; gap: 10px; padding: 10px;
  background: var(--app-bg-soft); border: 1px solid var(--app-border); border-radius: 12px;
}
.thumb {
  width: 72px; height: 72px; flex: 0 0 72px; border-radius: 8px; overflow: hidden;
  background: var(--app-bg); display: flex; align-items: center; justify-content: center;
}
.thumb-empty { color: var(--app-text-muted); }
.asset-body { flex: 1; min-width: 0; }
.asset-name { font-weight: 600; margin-bottom: 3px; }
.asset-desc {
  color: var(--app-text-muted); font-size: 12px; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.asset-actions { display: flex; gap: 6px; margin-top: 8px; }
.resolver {
  margin-top: 18px;
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card);
}
.resolver-desc { color: var(--app-text-secondary); font-size: 13px; margin-bottom: 12px; }
.resolver-desc code, .mono { font-family: var(--font-mono, monospace); }
.resolved { margin-top: 14px; }
.resolved-row { display: flex; gap: 14px; margin-bottom: 10px; }
.rk { flex: 0 0 84px; color: var(--app-text-muted); font-size: 13px; }
.rv { flex: 1; }
.rv.mono {
  background: var(--app-bg-soft); padding: 8px 12px; border-radius: 8px;
  border: 1px solid var(--app-border); line-height: 1.6; word-break: break-all;
}
.muted { color: var(--app-text-muted); }
</style>
