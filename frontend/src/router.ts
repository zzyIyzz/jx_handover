import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'batch-list', component: () => import('@/views/BatchList.vue') },
    { path: '/batch/:id', name: 'batch-edit', component: () => import('@/views/BatchEdit.vue') }
  ]
})

export default router
