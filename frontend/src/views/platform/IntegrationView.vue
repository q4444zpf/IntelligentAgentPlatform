<template>
  <div class="integration-page">
    <a-card>
      <h2>系统集成</h2>
      <p>统一认证、MCP 和外部智能体连接状态</p>
    </a-card>
    <a-card>
      <h3>认证方式</h3>
      <div v-if="authState">
        <strong>{{ authLabel }}</strong>
        <span v-if="authState.auth_method === 'oidc'"> · OIDC 会话有效</span>
        <span v-else-if="authState.auth_method === 'dev_test'"> · 仅开发环境可用</span>
        <span v-else> · 平台本地账号</span>
      </div>
      <span v-else>正在读取认证状态</span>
      <p v-if="authState?.auth_method === 'dev_test'">统一认证尚未配置，当前使用开发身份。</p>
    </a-card>
    <a-card>
      <h3>MCP 连接</h3>
      <div v-if="mcpState.length">
        <div v-for="client in mcpState" :key="client.key" class="connection-row">
          <span>{{ client.name }}</span>
          <span>{{ client.enabled && client.last_synced_at ? '已连接' : client.enabled ? '已启用，待同步' : '已停用' }}</span>
        </div>
      </div>
      <span v-else>暂无 MCP 客户端配置</span>
    </a-card>
    <a-alert v-if="error" type="error" :message="error" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { authApi, type AuthContext } from '@/api/auth';
import { mcpApi, type McpClient } from '@/api/mcp';

const authState = ref<AuthContext | null>(null);
const mcpState = ref<McpClient[]>([]);
const error = ref('');
const authLabel = computed(() => authState.value?.auth_method === 'oidc' ? '统一认证（OIDC）' : authState.value?.auth_method === 'dev_test' ? '开发身份' : '本地账号登录');

onMounted(async () => {
  const results = await Promise.allSettled([authApi.me(), mcpApi.list()]);
  const authResult = results[0];
  const mcpResult = results[1];
  if (authResult.status === 'fulfilled') authState.value = authResult.value;
  if (mcpResult.status === 'fulfilled') mcpState.value = mcpResult.value;
  const failures = results.filter((item): item is PromiseRejectedResult => item.status === 'rejected');
  if (failures.length) error.value = '部分集成状态读取失败，请稍后重试';
});
</script>

<style scoped>
.integration-page { display: grid; gap: 16px; }
.integration-page h2, .integration-page h3 { margin: 0 0 8px; }
.integration-page p { margin: 4px 0 0; color: #667085; }
.connection-row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #edf1f5; }
</style>
