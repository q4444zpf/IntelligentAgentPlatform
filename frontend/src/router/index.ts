import { createRouter, createWebHistory } from 'vue-router';

import { usePermissionStore } from '@/stores/permission';

import { routes } from './routes';

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const permissionStore = usePermissionStore();
  permissionStore.restoreSession();

  if (to.meta.public) {
    return permissionStore.isAuthenticated && to.path === '/login' ? '/dashboard' : true;
  }

  if (!permissionStore.isAuthenticated) {
    return {
      path: '/login',
      query: { redirect: to.fullPath },
    };
  }

  const permission = to.meta.permission as string | undefined;
  if (!permission) return true;

  if (permissionStore.hasPermission(permission)) return true;

  return '/dashboard';
});
