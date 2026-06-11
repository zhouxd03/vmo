<script setup>
import { ref } from 'vue'
import { NTabs, NTabPane } from 'naive-ui'
import PageHeader from '../components/PageHeader.vue'
import CredentialVault from '../components/CredentialVault.vue'
import GeneralSettings from '../components/GeneralSettings.vue'
import ThemePicker from '../components/ThemePicker.vue'

const tab = ref('credentials')
</script>

<template>
  <div>
    <PageHeader
      title="设置 / 凭据库"
      subtitle="集中管理 API Key、本地号池、生成参数和界面主题"
    />
    <n-tabs v-model:value="tab" type="line" animated>
      <n-tab-pane name="credentials" tab="凭据库">
        <CredentialVault />
      </n-tab-pane>
      <n-tab-pane name="general" tab="通用设置">
        <GeneralSettings />
      </n-tab-pane>
      <n-tab-pane name="appearance" tab="外观 / 主题">
        <ThemePicker />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>
