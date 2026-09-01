<template>
  <main class="forbidden-view">
    <a-result
      status="403"
      title="无权访问"
      sub-title="当前账号尚未分配可用权限，请联系管理员配置角色。"
    >
      <template #extra>
        <a-button type="primary" @click="logout">退出登录</a-button>
      </template>
    </a-result>
  </main>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';

import { usePermissionStore } from '@/stores/permission';

const router = useRouter();
const permissionStore = usePermissionStore();

async function logout(): Promise<void> {
  try {
    await permissionStore.logout();
  } catch {
    // The store clears local session state even when the server request fails.
  } finally {
    await router.replace('/login');
  }
}
</script>

<style scoped>
.forbidden-view {
  display: grid;
  min-height: 100vh;
  place-items: center;
  background: #f5f7fa;
}
</style>
