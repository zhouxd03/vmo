<script setup>
import { NIcon } from 'naive-ui'
import { ConstructOutline } from '@vicons/ionicons5'
import PageHeader from './PageHeader.vue'

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  phase: { type: String, default: '' },
  points: { type: Array, default: () => [] },
})
</script>

<template>
  <div>
    <PageHeader :title="title" :subtitle="subtitle" />
    <div class="empty-state">
      <n-icon :component="ConstructOutline" size="40" color="var(--app-accent)" />
      <div class="ph-phase" v-if="phase">{{ phase }}</div>
      <div class="ph-title">该模块将在后续阶段实现</div>
      <ul class="ph-points" v-if="points.length">
        <li v-for="(p, i) in points" :key="i">{{ p }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.ph-phase {
  color: var(--app-accent);
  font-weight: 700;
  letter-spacing: 1px;
}
.ph-title {
  color: var(--app-text-secondary);
  font-size: 15px;
}
.ph-points {
  text-align: left;
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--app-text-muted);
  line-height: 1.9;
}
</style>
