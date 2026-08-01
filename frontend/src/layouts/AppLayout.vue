<template>
  <a-layout class="water-shell" :class="{ 'has-secondary': activeGroup?.children?.length }">
    <header class="global-header">
      <div class="global-identity">
        <button class="brand-button" aria-label="返回 AI 对话" @click="router.push('/chat')">水</button>
        <button class="platform-brand" aria-label="返回 AI 对话" @click="router.push('/chat')">水利智能体平台</button>
      </div>
      <div class="header-main">
        <button class="mobile-menu" aria-label="打开导航" @click="mobileNavOpen = true"><MenuOutlined /></button>
        <div class="header-context" aria-label="当前位置">
          <span class="header-context-icon"><component :is="menuIcon(activeGroup?.icon)" /></span>
          <div>
            <small>{{ headerSectionLabel }}</small>
            <strong>{{ route.meta.title }}</strong>
          </div>
        </div>
        <div class="header-actions">
          <a-tooltip title="全局搜索"><a-button type="text" aria-label="全局搜索"><SearchOutlined /></a-button></a-tooltip>
          <a-badge :count="3" size="small"><a-button type="text" aria-label="通知"><BellOutlined /></a-button></a-badge>
          <a-button class="assistant-trigger" @click="assistantOpen = true"><RobotOutlined /> 快捷智能体助手</a-button>
          <a-dropdown>
            <button class="user-button"><span>{{ permissionStore.userName.slice(0, 2) }}</span><DownOutlined /></button>
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
        </div>
      </div>
      <div class="basin-status" aria-hidden="true"><i /><i /><i /><i /></div>
    </header>

    <aside class="primary-rail">
      <nav class="primary-nav" aria-label="主导航">
        <button
          v-for="menu in visibleMenus"
          :key="menu.key"
          class="primary-nav-item"
          :class="{ active: activeGroup?.key === menu.key }"
          :title="menu.title"
          @click="openGroup(menu)"
        >
          <component :is="menuIcon(menu.icon)" />
          <span>{{ menu.title }}</span>
          <em v-if="menu.key === 'chat'">2</em>
        </button>
      </nav>
    </aside>

    <aside v-if="activeGroup?.children?.length" class="secondary-nav">
      <div class="secondary-label">功能菜单</div>
      <nav>
        <button
          v-for="child in visibleChildren"
          :key="child.key"
          :class="{ active: route.path === child.path }"
          @click="router.push(child.path)"
        >
          <span>{{ child.title }}</span>
          <RightOutlined />
        </button>
      </nav>
    </aside>

    <a-layout class="workspace-shell">
      <a-layout-content class="app-content"><RouterView /></a-layout-content>
    </a-layout>

    <a-drawer v-model:open="assistantOpen" title="快捷智能体助手" width="420" class="assistant-drawer">
      <div class="assistant-agent">
        <span>智</span>
        <div><strong>平台运维助手</strong><small>当前页面上下文已连接</small></div>
      </div>
      <div class="assistant-context"><span>当前页面</span><b>{{ route.meta.title }}</b><span>当前项目</span><b>水利智能体平台</b></div>
      <div class="assistant-message">可以直接询问当前页面中的配置、运行状态和权限问题。高风险操作仍需审批。</div>
      <div class="assistant-input">输入问题或任务</div>
      <a-button type="primary" block @click="openFullChat">进入完整 AI 对话</a-button>
    </a-drawer>

    <a-drawer v-model:open="mobileNavOpen" placement="left" width="300" title="水利智能体平台">
      <div class="mobile-groups">
        <section v-for="menu in visibleMenus" :key="menu.key">
          <button @click="openMobileMenu(menu)"><component :is="menuIcon(menu.icon)" />{{ menu.title }}</button>
          <a v-for="child in visibleMenuChildren(menu)" :key="child.key" @click="openMobilePath(child.path)">{{ child.title }}</a>
        </section>
      </div>
    </a-drawer>
  </a-layout>
</template>

<script setup lang="ts">
import {
  ApiOutlined,
  AppstoreOutlined,
  BellOutlined,
  ClusterOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DownOutlined,
  ExperimentOutlined,
  MenuOutlined,
  MessageOutlined,
  RightOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue';
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { appMenus, type AppMenuItem } from '@/router/routes';
import { usePermissionStore } from '@/stores/permission';

const route = useRoute();
const router = useRouter();
const permissionStore = usePermissionStore();
const assistantOpen = ref(false);
const mobileNavOpen = ref(false);

const iconMap = {
  chat: MessageOutlined, dashboard: DashboardOutlined, agent: ClusterOutlined, capability: ExperimentOutlined,
  resources: AppstoreOutlined, operations: ApiOutlined, security: SafetyCertificateOutlined,
  system: SettingOutlined, default: DatabaseOutlined,
};

const visibleMenus = computed(() => appMenus.filter((menu) => !menu.permission || permissionStore.hasPermission(menu.permission)));
const activeGroup = computed(() => visibleMenus.value.find((menu) => menu.path === route.path || menu.children?.some((child) => child.path === route.path)) || visibleMenus.value[0]);
const visibleChildren = computed(() => visibleMenuChildren(activeGroup.value));
const headerSectionLabel = computed(() => activeGroup.value?.children?.length ? activeGroup.value.title : '智能工作区');

function visibleMenuChildren(menu?: AppMenuItem) {
  return menu?.children?.filter((child) => !child.permission || permissionStore.hasPermission(child.permission)) || [];
}
function menuIcon(icon?: string) { return iconMap[icon as keyof typeof iconMap] || iconMap.default; }
function openGroup(menu: AppMenuItem) { router.push(visibleMenuChildren(menu)[0]?.path || menu.path); }
function openMobileMenu(menu: AppMenuItem) { if (!menu.children?.length) openMobilePath(menu.path); }
function openMobilePath(path: string) { mobileNavOpen.value = false; router.push(path); }
function openFullChat() { assistantOpen.value = false; router.push('/chat'); }
function switchRole(role: 'user' | 'admin') {
  permissionStore.switchRole(role);
  const permission = route.meta.permission as string | undefined;
  if (permission && !permissionStore.hasPermission(permission)) router.push('/chat');
}
function logout() { permissionStore.logout(); router.replace('/login'); }
</script>

<style scoped>
.water-shell { display: grid; grid-template-rows: 67px minmax(0, 1fr); grid-template-columns: 72px minmax(0, 1fr); min-height: 100vh; min-height: 100dvh; background: #f7fafd; }
.water-shell.has-secondary { grid-template-columns: 72px 210px minmax(0, 1fr); }
.global-header { z-index: 5; display: grid; grid-row: 1; grid-column: 1 / -1; grid-template-rows: 64px 3px; grid-template-columns: 282px minmax(0, 1fr); background: #fff; box-shadow: 0 1px 0 #dce7ef; }
.global-identity { display: grid; grid-row: 1; grid-column: 1; grid-template-columns: 72px 210px; min-width: 0; border-right: 1px solid #dce7ef; }
.brand-button { display: grid; width: 44px; height: 44px; margin: 10px auto; place-items: center; color: #fff; background: linear-gradient(145deg, #2580f7, #1755d7); border: 0; border-radius: 7px; box-shadow: 0 6px 14px rgb(37 99 235 / 24%); font-size: 19px; font-weight: 900; cursor: pointer; transition: transform .18s ease, box-shadow .18s ease; }
.brand-button:hover { box-shadow: 0 8px 18px rgb(37 99 235 / 30%); transform: translateY(-1px); }
.platform-brand { display: flex; min-width: 0; align-items: center; padding: 0 20px; color: #24415b; background: #fff; border: 0; border-left: 1px solid #edf2f6; cursor: pointer; font-size: 18px; font-weight: 700; text-align: left; }
.brand-button:focus-visible,.platform-brand:focus-visible { outline: 2px solid #1689d8; outline-offset: -2px; }
.header-main { display: flex; min-width: 0; grid-row: 1; grid-column: 2; align-items: center; justify-content: space-between; gap: 12px; padding: 0 20px; background: #fff; }
.header-context { display: flex; min-width: 0; align-items: center; gap: 10px; }
.header-context-icon { display: grid; width: 34px; height: 34px; flex: none; place-items: center; color: #087ea4; background: #e7f8fb; border: 1px solid #c8edf2; border-radius: 6px; font-size: 16px; }
.header-context div { min-width: 0; }
.header-context small,.header-context strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.header-context small { margin-bottom: 1px; color: #8295a5; font-size: 12px; line-height: 17px; }
.header-context strong { color: #1d3b53; font-size: 14px; line-height: 20px; }
.primary-rail { z-index: 3; width: 72px; grid-row: 2; grid-column: 1; padding: 10px 8px; background: #fff; border-right: 1px solid #dce7ef; }
.primary-nav { display: grid; gap: 3px; }
.primary-nav-item { position: relative; display: flex; width: 56px; height: 50px; align-items: center; justify-content: center; flex-direction: column; gap: 2px; color: #7890a2; background: transparent; border: 1px solid transparent; border-radius: 6px; cursor: pointer; font-size: 9px; }
.primary-nav-item :deep(.anticon) { font-size: 17px; }
.primary-nav-item:hover { color: #1f5cc5; background: #f1f6ff; }
.primary-nav-item.active { color: #1e58bd; background: #eaf2ff; border-color: #cddfff; font-weight: 700; }
.primary-nav-item.active::before { position: absolute; left: -9px; width: 3px; height: 28px; content: ''; background: #2563eb; border-radius: 0 3px 3px 0; }
.primary-nav-item em { position: absolute; right: 4px; top: 3px; display: grid; width: 16px; height: 16px; place-items: center; color: #81500b; background: #f8c45d; border: 2px solid #fff; border-radius: 50%; font-size: 8px; font-style: normal; }
.secondary-nav { position: relative; width: 210px; grid-row: 2; grid-column: 2; padding: 0 12px; background: #fafdff; border-right: 1px solid #dce7ef; }
.secondary-label { margin: 17px 8px 7px; color: #91a1ad; font-size: 10px; font-weight: 700; }
.secondary-nav nav { display: grid; gap: 3px; }.secondary-nav nav button { display: flex; min-height: 38px; align-items: center; justify-content: space-between; padding: 0 10px; color: #607688; background: transparent; border: 0; border-radius: 5px; cursor: pointer; text-align: left; }
.secondary-nav nav button :deep(.anticon) { opacity: 0; font-size: 10px; }.secondary-nav nav button:hover,.secondary-nav nav button.active { color: #205abf; background: #eaf2ff; font-weight: 700; }.secondary-nav nav button.active :deep(.anticon) { opacity: 1; }
.workspace-shell { min-width: 0; max-width: 100vw; grid-row: 2; grid-column: 2; background: #f7fafd; }
.has-secondary .workspace-shell { grid-column: 3; }
.header-actions { display: flex; margin-left: auto; align-items: center; gap: 10px; }.assistant-trigger { color: #fff; background: #2563eb; border-color: #1d55cb; box-shadow: 0 4px 11px rgb(37 99 235 / 16%); }.assistant-trigger:hover { color: #fff !important; background: #1e57d0 !important; border-color: #1e57d0 !important; }
.user-button { display: flex; align-items: center; gap: 7px; color: #526a7b; background: transparent; border: 0; cursor: pointer; }.user-button span { display: grid; width: 30px; height: 30px; place-items: center; color: #2059b8; background: #e7f0ff; border: 1px solid #c9dcfb; border-radius: 5px; font-size: 11px; }
.basin-status { display: grid; grid-row: 2; grid-column: 1 / -1; grid-template-columns: 38% 30% 20% 12%; height: 3px; }.basin-status i:nth-child(1) { background: #0891b2; }.basin-status i:nth-child(2) { background: #2563eb; }.basin-status i:nth-child(3) { background: #16a47a; }.basin-status i:nth-child(4) { background: #f2b84b; }
.app-content { min-width: 0; max-width: 100%; padding: 18px; overflow-x: hidden; background: #f7fafd; }.mobile-menu { display: none; flex: none; color: #28506e; background: transparent; border: 0; font-size: 18px; }
.assistant-agent { display: flex; align-items: center; gap: 10px; }.assistant-agent > span { display: grid; width: 38px; height: 38px; place-items: center; color: #fff; background: #0891b2; border-radius: 6px; font-weight: 800; }.assistant-agent strong,.assistant-agent small { display: block; }.assistant-agent small { margin-top: 3px; color: #738796; }
.assistant-context { display: grid; grid-template-columns: 80px 1fr; gap: 8px; margin: 18px 0; padding: 12px; background: #f2f7fb; border: 1px solid #dce7ef; border-radius: 6px; font-size: 12px; }.assistant-context span { color: #7b8e9c; }.assistant-message { margin-bottom: 16px; padding: 12px; background: #fff; border: 1px solid #dce7ef; border-radius: 6px; line-height: 1.7; }.assistant-input { min-height: 90px; margin-bottom: 12px; padding: 12px; color: #91a0aa; background: #fbfdff; border: 1px solid #ccdbe6; border-radius: 6px; }
.mobile-groups { display: grid; gap: 14px; }.mobile-groups section { display: grid; gap: 4px; }.mobile-groups button { display: flex; align-items: center; gap: 8px; padding: 8px; color: #183047; background: #edf5ff; border: 0; border-radius: 5px; font-weight: 700; }.mobile-groups a { padding: 7px 12px; color: #607688; cursor: pointer; }
/* Enterprise console typography: 14px controls, 12px supporting text. */
.primary-nav-item { height: 56px; font-size: 12px; line-height: 16px; }
.primary-nav-item em { width: 18px; height: 18px; font-size: 12px; }
.secondary-label { font-size: 12px; line-height: 18px; }
.secondary-nav nav button { min-height: 42px; font-size: 14px; line-height: 22px; }
.user-button span { font-size: 12px; }
.assistant-agent small,.assistant-context { font-size: 12px; line-height: 18px; }
.assistant-message,.assistant-input,.mobile-groups { font-size: 14px; line-height: 22px; }
@media (max-width: 1180px) { .water-shell { display: block; }.global-header { position: relative; display: grid; height: 67px; grid-template-columns: 1fr; }.global-identity,.primary-rail,.secondary-nav { display: none; }.header-main { grid-column: 1; }.mobile-menu { display: inline-grid; place-items: center; }.workspace-shell { display: block; }.app-content { padding: 14px; } }
@media (max-width: 760px) { .assistant-trigger { width: 34px; padding-inline: 0; font-size: 0; }.assistant-trigger :deep(svg) { width: 15px; height: 15px; }.header-actions { gap: 3px; }.header-main { height: 58px; padding: 0 10px; }.header-context-icon { width: 30px; height: 30px; }.global-header { height: 61px; grid-template-rows: 58px 3px; }.app-content { padding: 10px; } }
@media (max-width: 480px) { .header-actions > :deep(.ant-btn),.assistant-trigger { display: none; }.header-context small { display: none; }.header-context strong { max-width: 120px; }.user-button { padding: 0; }.app-content { padding: 6px; } }
</style>
