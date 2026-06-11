import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'home', component: () => import('../views/HomeView.vue'), meta: { title: '概览', icon: 'home' } },
  { path: '/import', name: 'import', component: () => import('../views/ImportView.vue'), meta: { title: '项目导入', icon: 'import' } },
  { path: '/script', name: 'script', component: () => import('../views/ScriptView.vue'), meta: { title: '剧本解析', icon: 'script' } },
  { path: '/assets', name: 'assets', component: () => import('../views/AssetsView.vue'), meta: { title: '资产库', icon: 'assets' } },
  { path: '/worktable', name: 'worktable', component: () => import('../views/WorktableView.vue'), meta: { title: '批量工作台', icon: 'worktable', fullBleed: true, gen: true } },
  { path: '/library', name: 'library', component: () => import('../views/LibraryView.vue'), meta: { title: '批次库', icon: 'library' } },
  { path: '/templates', name: 'templates', component: () => import('../views/TemplatesView.vue'), meta: { title: '提示词模板', icon: 'templates' } },
  { path: '/doubao-pool', name: 'doubaoPool', component: () => import('../views/DoubaoPoolView.vue'), meta: { title: '豆包号池', icon: 'doubaoPool' } },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue'), meta: { title: '设置 / 凭据库', icon: 'settings' } },
  { path: '/logs', name: 'logs', component: () => import('../views/LogsView.vue'), meta: { title: '日志调试', icon: 'logs' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
