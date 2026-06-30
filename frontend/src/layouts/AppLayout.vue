<template>
  <a-layout class="app-shell">
    <a-layout-sider v-model:collapsed="collapsed" collapsible :width="248" class="app-sider">
      <div class="brand">
        <div class="brand-mark">智</div>
        <div v-if="!collapsed" class="brand-text">
          <strong>通用智能体平台</strong>
          <span>AgentOps Prototype</span>
        </div>
      </div>

      <a-menu
        v-model:openKeys="openKeys"
        v-model:selectedKeys="selectedKeys"
        theme="dark"
        mode="inline"
        :items="menuItems"
        @click="handleMenuClick"
      />
    </a-layout-sider>

    <a-layout>
      <a-layout-header class="app-header">
        <div>
          <div class="page-title">{{ route.meta.title || '综合态势' }}</div>
          <div class="page-subtitle">智能体、MCP、Skill、知识库、流程和大模型统一管理原型</div>
        </div>
        <a-space size="middle">
          <a-tag color="blue">Mock 数据</a-tag>
          <a-badge status="processing" text="沙箱模式" />
          <a-dropdown>
            <a-button>
              {{ permissionStore.userName }}
              <DownOutlined />
            </a-button>
            <template #overlay>
              <a-menu>
                <a-menu-item disabled>当前角色：{{ permissionStore.roleName }}</a-menu-item>
                <a-menu-divider />
                <a-menu-item @click="switchRole('user')">切换为普通用户</a-menu-item>
                <a-menu-item @click="switchRole('admin')">切换为管理员</a-menu-item>
                <a-menu-divider />
                <a-menu-item @click="logout">退出登录</a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </a-space>
      </a-layout-header>

      <a-layout-content class="app-content">
        <RouterView />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import {
  ApiOutlined,
  AppstoreOutlined,
  BarChartOutlined,
  ClusterOutlined,
  ControlOutlined,
  DashboardOutlined,
  DownOutlined,
  ExperimentOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons-vue';
import type { ItemType } from 'ant-design-vue';
import { computed, h, ref, watchEffect } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { appMenus } from '@/router/routes';
import { usePermissionStore } from '@/stores/permission';

const collapsed = ref(false);
const route = useRoute();
const router = useRouter();
const permissionStore = usePermissionStore();

const iconMap = {
  dashboard: DashboardOutlined,
  resources: SafetyCertificateOutlined,
  model: ExperimentOutlined,
  dispatch: ControlOutlined,
  monitor: BarChartOutlined,
  agent: ClusterOutlined,
  api: ApiOutlined,
  default: AppstoreOutlined,
};

const openKeys = ref<string[]>([]);
const selectedKeys = ref<string[]>([]);

const menuItems = computed<ItemType[]>(() =>
  appMenus
    .filter((menu) => !menu.permission || permissionStore.hasPermission(menu.permission))
    .map((menu) => ({
      key: menu.key,
      label: menu.title,
      icon: () => h(iconMap[menu.icon as keyof typeof iconMap] || iconMap.default),
      children: menu.children
        ?.filter((child) => !child.permission || permissionStore.hasPermission(child.permission))
        .map((child) => ({
          key: child.key,
          label: child.title,
        })),
    })),
);

const menuPathMap = computed(() => {
  const entries: Record<string, string> = {};
  appMenus.forEach((menu) => {
    entries[menu.key] = menu.path;
    menu.children?.forEach((child) => {
      entries[child.key] = child.path;
    });
  });
  return entries;
});

watchEffect(() => {
  const current = appMenus
    .flatMap((menu) => [menu, ...(menu.children || [])])
    .find((menu) => menu.path === route.path);

  selectedKeys.value = current ? [current.key] : ['dashboard'];
  const parent = appMenus.find((menu) => menu.children?.some((child) => child.path === route.path));
  openKeys.value = parent ? [parent.key] : openKeys.value;
});

function handleMenuClick({ key }: { key: string }) {
  const path = menuPathMap.value[key];
  if (path) router.push(path);
}

function switchRole(role: 'user' | 'admin') {
  permissionStore.switchRole(role);
  const permission = route.meta.permission as string | undefined;
  if (permission && !permissionStore.hasPermission(permission)) {
    router.push('/dashboard');
  }
}

function logout() {
  permissionStore.logout();
  router.replace('/login');
}
</script>
