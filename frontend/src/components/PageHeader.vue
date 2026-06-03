<script setup>
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
})
</script>

<template>
  <div class="page-header">
    <div>
      <h1 class="page-title">{{ title }}</h1>
      <p v-if="subtitle" class="page-sub">{{ subtitle }}</p>
    </div>
    <div class="page-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}
.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}
.page-sub {
  margin: 6px 0 0;
  color: var(--app-text-secondary);
  font-size: 13px;
}
.page-actions {
  display: flex;
  gap: 10px;
}
</style>
