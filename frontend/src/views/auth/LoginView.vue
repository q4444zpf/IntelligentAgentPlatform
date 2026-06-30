<template>
  <main class="login-page">
    <section class="login-hero" aria-label="平台能力概览">
      <div class="login-brand">
        <div class="login-brand-mark">智</div>
        <div>
          <strong>通用智能体平台</strong>
          <span>AgentOps for Water Conservancy</span>
        </div>
      </div>

      <div class="hero-copy">
        <a-tag color="blue">MVP 原型环境</a-tag>
        <h1>水利业务智能体统一运行入口</h1>
        <p>集中管理智能体、MCP、Skill、知识库、流程编排和多智能体协同，支持全链路审计与沙箱执行。</p>
      </div>

      <div class="capability-grid">
        <div v-for="item in capabilities" :key="item.title" class="capability-item">
          <component :is="item.icon" />
          <div>
            <strong>{{ item.title }}</strong>
            <span>{{ item.description }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="login-panel" aria-label="登录表单">
      <div class="login-card">
        <div class="login-heading">
          <span>欢迎回来</span>
          <h2>登录平台</h2>
        </div>

        <a-form layout="vertical" :model="form" @finish="handleLogin">
          <a-form-item
            label="账号"
            name="username"
            :rules="[{ required: true, message: '请输入账号' }]"
          >
            <a-input v-model:value="form.username" size="large" autocomplete="username" placeholder="admin 或 user">
              <template #prefix>
                <UserOutlined />
              </template>
            </a-input>
          </a-form-item>

          <a-form-item
            label="密码"
            name="password"
            :rules="[{ required: true, message: '请输入密码' }]"
          >
            <a-input-password
              v-model:value="form.password"
              size="large"
              autocomplete="current-password"
              placeholder="任意密码均可登录"
            >
              <template #prefix>
                <LockOutlined />
              </template>
            </a-input-password>
          </a-form-item>

          <div class="login-options">
            <a-checkbox v-model:checked="form.remember">记住登录状态</a-checkbox>
            <a-segmented v-model:value="form.role" :options="roleOptions" />
          </div>

          <a-button type="primary" html-type="submit" size="large" block :loading="submitting">
            登录
          </a-button>
        </a-form>

        <a-alert
          class="login-tip"
          type="info"
          show-icon
          message="演示账号"
          description="管理员可查看全部菜单，普通用户仅展示工作台、AI 对话、资源空间和大模型配置。"
        />
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import {
  ApiOutlined,
  ClusterOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { usePermissionStore, type UserRole } from '@/stores/permission';

const router = useRouter();
const route = useRoute();
const permissionStore = usePermissionStore();
const submitting = ref(false);

const form = reactive({
  username: 'admin',
  password: '123456',
  role: 'admin' as UserRole,
  remember: true,
});

const roleOptions = [
  { label: '管理员', value: 'admin' },
  { label: '普通用户', value: 'user' },
];

const capabilities = [
  {
    title: '资源统一管理',
    description: '智能体、MCP、Skill、Prompt、知识库和流程统一建模。',
    icon: SafetyCertificateOutlined,
  },
  {
    title: '智能体协同',
    description: '支持编排式多智能体任务执行与过程追踪。',
    icon: ClusterOutlined,
  },
  {
    title: '开放集成',
    description: '通过 OpenAPI 接入第三方 Web 系统和业务流程。',
    icon: ApiOutlined,
  },
];

async function handleLogin() {
  submitting.value = true;
  await new Promise((resolve) => window.setTimeout(resolve, 350));
  permissionStore.login({
    username: form.username,
    role: form.role,
    remember: form.remember,
  });
  message.success('登录成功');
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard';
  router.replace(redirect);
  submitting.value = false;
}
</script>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(420px, 0.95fr);
  min-height: 100vh;
  color: #172033;
  background:
    radial-gradient(circle at 12% 20%, rgb(63 139 201 / 16%), transparent 28%),
    linear-gradient(135deg, #f5f8fc 0%, #e9f1f8 52%, #f8fbfd 100%);
}

.login-hero {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 48px 56px;
}

.login-brand,
.capability-item {
  display: flex;
  align-items: center;
}

.login-brand {
  gap: 12px;
}

.login-brand-mark {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  color: #fff;
  font-size: 20px;
  font-weight: 800;
  background: #155fa0;
  border-radius: 8px;
}

.login-brand strong,
.capability-item strong {
  display: block;
}

.login-brand span,
.capability-item span,
.hero-copy p {
  color: #667085;
}

.hero-copy {
  max-width: 720px;
}

.hero-copy h1 {
  max-width: 680px;
  margin: 18px 0 16px;
  color: #10223b;
  font-size: 44px;
  line-height: 1.18;
}

.hero-copy p {
  max-width: 640px;
  margin: 0;
  font-size: 17px;
  line-height: 1.8;
}

.capability-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.capability-item {
  gap: 12px;
  min-height: 108px;
  padding: 18px;
  background: rgb(255 255 255 / 72%);
  border: 1px solid rgb(210 224 239 / 86%);
  border-radius: 8px;
}

.capability-item :deep(.anticon) {
  color: #155fa0;
  font-size: 24px;
}

.capability-item span {
  display: block;
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.55;
}

.login-panel {
  display: grid;
  place-items: center;
  padding: 48px;
}

.login-card {
  width: min(100%, 440px);
  padding: 34px;
  background: #fff;
  border: 1px solid #e0e8f2;
  border-radius: 8px;
  box-shadow: 0 22px 70px rgb(32 78 119 / 14%);
}

.login-heading {
  margin-bottom: 26px;
}

.login-heading span {
  color: #155fa0;
  font-weight: 700;
}

.login-heading h2 {
  margin: 6px 0 0;
  color: #10223b;
  font-size: 28px;
}

.login-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 22px;
}

.login-tip {
  margin-top: 20px;
}

@media (max-width: 960px) {
  .login-page {
    grid-template-columns: 1fr;
  }

  .login-hero {
    gap: 32px;
    padding: 32px 24px 0;
  }

  .hero-copy h1 {
    font-size: 32px;
  }

  .capability-grid {
    grid-template-columns: 1fr;
  }

  .login-panel {
    padding: 24px;
  }
}
</style>
