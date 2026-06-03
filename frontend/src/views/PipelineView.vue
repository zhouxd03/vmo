<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NSelect, NButton, NIcon, NCard, NEmpty, NTag, NImage, NSpin, NSwitch,
  NInput, NPopconfirm, NTooltip, NImageGroup, useMessage,
} from 'naive-ui'
import {
  RefreshOutline, FilmOutline, GitMergeOutline, ImageOutline,
  EaselOutline, CheckmarkDoneOutline, FlashOutline,
} from '@vicons/ionicons5'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const message = useMessage()
const store = useProjectStore()

const state = ref(null)        // story_state: {current, shots:{shot_no→snap}}
const loading = ref(false)
const busy = ref({})           // shot_no → action key currently running
const opts = ref({ use_llm: false, ai_review: true, model: '' })

onMounted(async () => {
  await store.refreshList()
  if (!store.current && store.projects.length) await store.select(store.projects[0].id)
  if (store.current) await loadState()
})

const projectOptions = computed(() =>
  store.projects.map((p) => ({ label: `${p.name}（${p.episode_count || 1}集·${p.shot_count || 0}镜）`, value: p.id })))

const shots = computed(() => store.currentEpisode?.shots || [])
const current = computed(() => state.value?.current || null)
function snap(shotNo) { return state.value?.shots?.[shotNo] || null }

async function onSelectProject(pid) {
  await store.select(pid)
  await loadState()
}

async function loadState() {
  if (!store.current) return
  loading.value = true
  try {
    state.value = await api.getContinuity(store.current.id)
  } catch (e) { message.error(e.message) } finally { loading.value = false }
}

async function resetState() {
  state.value = await api.resetContinuity(store.current.id)
  message.success('已重置连续性记忆')
}

function prevStateOf(idx) {
  // story_state.current is the live accumulator; for per-shot preview we use the
  // snapshot of the previous shot if it exists, else null (first shot).
  if (idx <= 0) return null
  const prev = shots.value[idx - 1]
  return snap(prev.shot_no) || null
}

function setBusy(shotNo, key) { busy.value = { ...busy.value, [shotNo]: key } }
function clearBusy(shotNo) { const b = { ...busy.value }; delete b[shotNo]; busy.value = b }

async function decide(shot, idx) {
  setBusy(shot.shot_no, 'decide')
  try {
    await api.decideHandoff(store.current.id, {
      shot, prev_state: prevStateOf(idx), use_llm: opts.value.use_llm,
      model: opts.value.model || undefined, commit: true,
    })
    await loadState()
    message.success(`${shot.shot_no} 衔接决策完成`)
  } catch (e) { message.error(e.message) } finally { clearBusy(shot.shot_no) }
}

async function genStaging(shot, idx) {
  setBusy(shot.shot_no, 'staging')
  try {
    await api.genStaging(store.current.id, { shot, prev_state: prevStateOf(idx), model: opts.value.model || undefined })
    await loadState()
    message.success(`${shot.shot_no} 站位图已生成`)
  } catch (e) { message.error(e.message) } finally { clearBusy(shot.shot_no) }
}

async function genDirector(shot, idx) {
  setBusy(shot.shot_no, 'director')
  try {
    await api.genDirectorBoard(store.current.id, { shot, prev_state: prevStateOf(idx), model: opts.value.model || undefined })
    await loadState()
    message.success(`${shot.shot_no} 导演图已生成`)
  } catch (e) { message.error(e.message) } finally { clearBusy(shot.shot_no) }
}

async function review(shot, idx) {
  setBusy(shot.shot_no, 'review')
  try {
    await api.reviewContinuity(store.current.id, { shot, prev_state: prevStateOf(idx), model: opts.value.model || undefined })
    await loadState()
    message.success(`${shot.shot_no} AI 复核完成`)
  } catch (e) { message.error(e.message) } finally { clearBusy(shot.shot_no) }
}

function imgUrl(filename) {
  return filename ? api.continuityImageUrl(store.current.id, filename) : null
}
function names(items) {
  return (items || []).map((it) => (typeof it === 'string' ? it : it?.name)).filter(Boolean)
}
function isBusy(shotNo, key) { return busy.value[shotNo] === key }
function anyBusy(shotNo) { return !!busy.value[shotNo] }
</script>

<template>
  <div>
    <PageHeader title="分镜流水线"
      subtitle="连续性引擎：StoryState 记忆层 + 尾帧/站位图/导演图中继 + AI 多模态复核">
      <template #actions>
        <n-select :value="store.currentId" :options="projectOptions" placeholder="选择项目"
          style="width: 240px" @update:value="onSelectProject" />
        <n-popconfirm v-if="store.current" @positive-click="resetState">
          <template #trigger>
            <n-button size="small" quaternary>
              <template #icon><n-icon :component="RefreshOutline" /></template>重置记忆
            </n-button>
          </template>
          清空本项目的连续性记忆（StoryState）？分镜与中间产物文件不删除。
        </n-popconfirm>
      </template>
    </PageHeader>

    <n-empty v-if="!store.current" description="请先在「项目导入」导入一个项目">
      <template #extra><n-button size="small" @click="router.push('/import')">去导入</n-button></template>
    </n-empty>
    <n-empty v-else-if="!shots.length" description="该项目还没有分镜，请先在「剧本解析」完成阶段二拆解">
      <template #extra><n-button size="small" @click="router.push('/parse')">去解析</n-button></template>
    </n-empty>

    <div v-else class="layout">
      <!-- left: live state + options -->
      <div class="side">
        <n-card title="连续性记忆（当前）" :bordered="false" class="panel">
          <div v-if="!current?.shot_no" class="hint">尚未提交任何分镜，记忆为空。逐镜决策/生成后将在此累积。</div>
          <div v-else class="state">
            <div class="srow"><span class="k">当前镜</span><span class="v mono">{{ current.shot_no }}</span></div>
            <div class="srow"><span class="k">场景</span><span class="v">{{ current.scene || '—' }}</span></div>
            <div class="srow"><span class="k">在场人物</span>
              <span class="v"><n-tag v-for="c in current.characters" :key="c.name || c" size="tiny" :bordered="false" type="success">{{ c.name || c }}</n-tag><template v-if="!current.characters?.length">—</template></span>
            </div>
            <div class="srow"><span class="k">道具</span>
              <span class="v"><n-tag v-for="p in current.props" :key="p.name || p" size="tiny" :bordered="false">{{ p.name || p }}</n-tag><template v-if="!current.props?.length">—</template></span>
            </div>
            <div class="srow"><span class="k">光线/情绪</span><span class="v">{{ current.lighting || '—' }}</span></div>
            <div class="srow"><span class="k">导演接口</span><span class="v">{{ current.director_note || '—' }}</span></div>
            <div class="srow"><span class="k">上一镜尾帧</span>
              <span class="v">
                <n-image v-if="imgUrl(current.tail_frame)" :src="imgUrl(current.tail_frame)" width="120" />
                <template v-else>无</template>
              </span>
            </div>
          </div>
        </n-card>

        <n-card title="决策选项" :bordered="false" class="panel">
          <div class="opt"><span>LLM 升级决策</span><n-switch v-model:value="opts.use_llm" size="small" /></div>
          <div class="opthint">关：规则引擎（无需凭据）。开：多模态 LLM 判断，失败自动回退规则。</div>
          <div class="opt"><span>AI 连续性复核闸门</span><n-switch v-model:value="opts.ai_review" size="small" /></div>
          <div class="opthint">全自动模式下强制插入一次复核，把尾帧/站位图发给视觉 LLM 校验衔接。</div>
          <div class="field"><label>模型名（留空用默认 LLM 凭据）</label>
            <n-input v-model:value="opts.model" size="small" placeholder="如 gpt-4o / 自定义模型名" />
          </div>
        </n-card>
      </div>

      <!-- main: shot chain -->
      <div class="detail">
        <n-spin :show="loading">
          <div class="chain">
            <n-card v-for="(s, idx) in shots" :key="s.shot_no" :bordered="false" class="shot">
              <div class="shead">
                <div class="sleft">
                  <span class="mono sno">{{ s.shot_no }}</span>
                  <span class="scene">{{ s.scene || '（无场景）' }}</span>
                </div>
                <div class="schips">
                  <n-tag v-for="c in names(s.characters)" :key="'c'+c" size="tiny" :bordered="false" type="success">@{{ c }}</n-tag>
                  <n-tag v-for="p in names(s.props)" :key="'p'+p" size="tiny" :bordered="false">${{ p }}</n-tag>
                </div>
              </div>
              <div v-if="s.action" class="action">{{ s.action }}</div>

              <!-- decision -->
              <div class="decision">
                <template v-if="snap(s.shot_no)?.decision">
                  <div class="dbadges">
                    <n-tag size="small" :type="snap(s.shot_no).decision.scene_cut ? 'warning' : 'default'" :bordered="false">
                      <template #icon><n-icon :component="GitMergeOutline" /></template>
                      {{ snap(s.shot_no).decision.scene_cut ? '切场景·直接生成' : '时空连续' }}
                    </n-tag>
                    <n-tag v-if="snap(s.shot_no).decision.use_tail_frame" size="small" type="info" :bordered="false">
                      <template #icon><n-icon :component="FilmOutline" /></template>尾帧承接
                    </n-tag>
                    <n-tag v-if="snap(s.shot_no).decision.use_staging" size="small" type="info" :bordered="false">
                      <template #icon><n-icon :component="ImageOutline" /></template>站位图
                    </n-tag>
                    <n-tag v-if="snap(s.shot_no).decision.use_director_board" size="small" type="info" :bordered="false">
                      <template #icon><n-icon :component="EaselOutline" /></template>导演图
                    </n-tag>
                    <n-tag size="tiny" :bordered="false">{{ snap(s.shot_no).decision.source === 'llm' ? 'LLM' : '规则' }}</n-tag>
                  </div>
                  <div class="reason">{{ snap(s.shot_no).decision.reason }}</div>
                </template>
                <div v-else class="reason muted">未决策——点「衔接决策」让引擎判断本镜如何承接上一镜。</div>
              </div>

              <!-- intermediate products -->
              <n-image-group>
                <div class="thumbs">
                  <div v-if="idx > 0 && imgUrl(prevStateOf(idx)?.tail_frame)" class="thumb">
                    <n-image :src="imgUrl(prevStateOf(idx).tail_frame)" object-fit="cover" />
                    <span class="tlabel">上一镜尾帧</span>
                  </div>
                  <div v-if="imgUrl(snap(s.shot_no)?.staging_image)" class="thumb">
                    <n-image :src="imgUrl(snap(s.shot_no).staging_image)" object-fit="cover" />
                    <span class="tlabel">站位图</span>
                  </div>
                  <div v-if="imgUrl(snap(s.shot_no)?.director_board)" class="thumb">
                    <n-image :src="imgUrl(snap(s.shot_no).director_board)" object-fit="cover" />
                    <span class="tlabel">导演图</span>
                  </div>
                </div>
              </n-image-group>

              <!-- review verdict -->
              <div v-if="snap(s.shot_no)?.review" class="review" :class="snap(s.shot_no).review.pass ? 'ok' : 'bad'">
                <n-icon :component="CheckmarkDoneOutline" />
                <span>AI 复核：{{ snap(s.shot_no).review.pass ? '通过' : '未通过' }} · {{ snap(s.shot_no).review.score }}分</span>
                <span v-if="snap(s.shot_no).review.issues?.length" class="issues">问题：{{ snap(s.shot_no).review.issues.join('；') }}</span>
              </div>

              <!-- actions -->
              <div class="acts">
                <n-button size="tiny" :loading="isBusy(s.shot_no,'decide')" :disabled="anyBusy(s.shot_no)" @click="decide(s, idx)">
                  <template #icon><n-icon :component="FlashOutline" /></template>衔接决策
                </n-button>
                <n-tooltip trigger="hover"><template #trigger>
                  <n-button size="tiny" :loading="isBusy(s.shot_no,'staging')" :disabled="anyBusy(s.shot_no)" @click="genStaging(s, idx)">站位图</n-button>
                </template>gpt-image 生成俯视站位图（需生图凭据）</n-tooltip>
                <n-tooltip trigger="hover"><template #trigger>
                  <n-button size="tiny" :loading="isBusy(s.shot_no,'director')" :disabled="anyBusy(s.shot_no)" @click="genDirector(s, idx)">导演图</n-button>
                </template>gpt-image 生成 16:9 导演分镜蓝图（需生图凭据）</n-tooltip>
                <n-tooltip trigger="hover"><template #trigger>
                  <n-button size="tiny" :loading="isBusy(s.shot_no,'review')" :disabled="anyBusy(s.shot_no)" @click="review(s, idx)">AI 复核</n-button>
                </template>把尾帧/站位图发给视觉 LLM 校验衔接（需视觉 LLM 凭据）</n-tooltip>
              </div>
            </n-card>
          </div>
        </n-spin>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 320px 1fr; gap: 16px; }
@media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }
.panel {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card); margin-bottom: 16px;
}
.side { min-width: 0; }
.hint { font-size: 12px; color: var(--app-text-muted); }
.state { display: flex; flex-direction: column; gap: 9px; }
.srow { display: grid; grid-template-columns: 76px 1fr; gap: 8px; font-size: 13px; align-items: start; }
.srow .k { color: var(--app-text-muted); font-size: 12px; }
.srow .v { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.mono { font-family: var(--font-mono, monospace); }
.opt { display: flex; align-items: center; justify-content: space-between; font-size: 13px; margin-top: 8px; }
.opthint { font-size: 11px; color: var(--app-text-muted); margin: 3px 0 6px; line-height: 1.4; }
.field { margin-top: 10px; }
.field > label { display: block; font-size: 12px; color: var(--app-text-muted); margin-bottom: 5px; }

.detail { min-width: 0; }
.chain { display: flex; flex-direction: column; gap: 14px; }
.shot {
  background: color-mix(in srgb, var(--app-surface) 82%, transparent);
  border: 1px solid var(--app-border); border-radius: var(--r-card);
}
.shead { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.sleft { display: flex; align-items: baseline; gap: 10px; }
.sno { font-size: 14px; color: var(--app-accent); font-weight: 700; }
.scene { font-size: 14px; font-weight: 600; }
.schips { display: flex; flex-wrap: wrap; gap: 4px; }
.action { font-size: 12px; color: var(--app-text-muted); margin-top: 8px; line-height: 1.5; }
.decision { margin-top: 10px; }
.dbadges { display: flex; flex-wrap: wrap; gap: 6px; }
.reason { font-size: 12px; color: var(--app-text-muted); margin-top: 6px; line-height: 1.5; }
.reason.muted { font-style: italic; }
.thumbs { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.thumb {
  width: 132px; border: 1px solid var(--app-border); border-radius: 8px; overflow: hidden;
  background: var(--app-bg);
}
.thumb :deep(img) { width: 132px; height: 80px; object-fit: cover; display: block; }
.tlabel { display: block; font-size: 11px; color: var(--app-text-muted); padding: 3px 6px; text-align: center; }
.review {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 12px; padding: 7px 10px; border-radius: 8px; font-size: 12px;
}
.review.ok { background: color-mix(in srgb, var(--app-accent) 14%, transparent); color: var(--app-accent); }
.review.bad { background: color-mix(in srgb, #ff6b6b 14%, transparent); color: #ff8585; }
.review .issues { color: var(--app-text-muted); }
.acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--app-border); }
</style>
