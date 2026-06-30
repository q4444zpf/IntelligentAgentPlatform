import type { RouteRecordRaw } from 'vue-router';

import AppLayout from '@/layouts/AppLayout.vue';

export interface AppMenuItem {
  key: string;
  title: string;
  path: string;
  icon?: string;
  permission?: string;
  children?: AppMenuItem[];
}

export const appMenus: AppMenuItem[] = [
  { key: 'dashboard', title: '工作台', path: '/dashboard', icon: 'dashboard', permission: 'dashboard:view' },
  { key: 'chat', title: 'AI 对话', path: '/chat', icon: 'agent', permission: 'chat:view' },
  {
    key: 'personal',
    title: '个人资源空间',
    path: '/personal',
    icon: 'resources',
    permission: 'resources:personal',
    children: [
      { key: 'my-agents', title: '我的智能体', path: '/personal/agents', permission: 'resources:personal' },
      { key: 'my-mcp', title: '我的 MCP', path: '/personal/mcp', permission: 'resources:personal' },
      { key: 'my-skills', title: '我的 Skill', path: '/personal/skills', permission: 'resources:personal' },
      { key: 'my-models', title: '我的大模型', path: '/llm', permission: 'platform:llm' },
      { key: 'my-publish', title: '我的发布申请', path: '/personal/publish', permission: 'resources:personal' },
    ],
  },
  {
    key: 'public',
    title: '公用资源库',
    path: '/public',
    icon: 'public',
    permission: 'resources:public',
    children: [
      { key: 'public-agents', title: '公用智能体', path: '/public/agents', permission: 'resources:public' },
      { key: 'public-mcp', title: '公用 MCP', path: '/public/mcp', permission: 'resources:public' },
      { key: 'public-skills', title: '公用 Skill', path: '/public/skills', permission: 'resources:public' },
      { key: 'publish-review', title: '公用资源审核', path: '/public/review', permission: 'publish:review' },
    ],
  },
  { key: 'agent-manage', title: '智能体管理', path: '/agent/manage', icon: 'agent', permission: 'agent:manage' },
  { key: 'prompt', title: 'Prompt 管理', path: '/prompt', icon: 'prompt', permission: 'prompt:view' },
  { key: 'mcp', title: 'MCP 管理', path: '/mcp', icon: 'mcp', permission: 'mcp:view' },
  { key: 'skill', title: 'Skill / Tool 管理', path: '/skill', icon: 'skill', permission: 'skill:view' },
  { key: 'knowledge', title: '知识库管理', path: '/knowledge', icon: 'knowledge', permission: 'knowledge:view' },
  { key: 'workflow', title: '流程编排', path: '/workflow', icon: 'workflow', permission: 'workflow:view' },
  { key: 'collaboration', title: '多智能体协同', path: '/collaboration', icon: 'collaboration', permission: 'collaboration:view' },
  { key: 'llm', title: '大模型配置', path: '/llm', icon: 'api', permission: 'platform:llm' },
  { key: 'integration', title: '系统集成', path: '/integration', icon: 'api', permission: 'integration:view' },
  {
    key: 'system',
    title: '系统管理',
    path: '/system',
    icon: 'system',
    permission: 'system:view',
    children: [
      { key: 'users', title: '用户与权限', path: '/system/users', permission: 'system:users' },
      { key: 'audit', title: '审计与日志', path: '/system/audit', permission: 'system:audit' },
      { key: 'sandbox', title: '沙箱监控', path: '/system/sandbox', permission: 'platform:sandbox' },
      { key: 'settings', title: '系统设置', path: '/system/settings', permission: 'system:settings' },
    ],
  },
];

const genericView = () => import('@/views/platform/GenericModuleView.vue');
const resourceView = () => import('@/views/resources/ResourceListView.vue');

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/auth/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: AppLayout,
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'Dashboard', component: () => import('@/views/dashboard/DashboardView.vue'), meta: { title: '工作台', permission: 'dashboard:view' } },
      { path: 'chat', name: 'Chat', component: () => import('@/views/agent/AgentConsoleView.vue'), meta: { title: 'AI 对话', permission: 'chat:view' } },
      { path: 'personal/agents', name: 'MyAgents', component: resourceView, meta: { title: '我的智能体', permission: 'resources:personal' } },
      { path: 'personal/mcp', name: 'MyMcp', component: resourceView, meta: { title: '我的 MCP', permission: 'resources:personal' } },
      { path: 'personal/skills', name: 'MySkills', component: resourceView, meta: { title: '我的 Skill', permission: 'resources:personal' } },
      { path: 'personal/publish', name: 'MyPublish', component: resourceView, meta: { title: '我的发布申请', permission: 'resources:personal' } },
      { path: 'public/agents', name: 'PublicAgents', component: resourceView, meta: { title: '公用智能体', permission: 'resources:public' } },
      { path: 'public/mcp', name: 'PublicMcp', component: resourceView, meta: { title: '公用 MCP', permission: 'resources:public' } },
      { path: 'public/skills', name: 'PublicSkills', component: resourceView, meta: { title: '公用 Skill', permission: 'resources:public' } },
      { path: 'public/review', name: 'PublishReview', component: resourceView, meta: { title: '发布审核', permission: 'publish:review' } },
      { path: 'agent/manage', name: 'AgentManage', component: genericView, meta: { title: '智能体管理', module: 'agent', permission: 'agent:manage' } },
      { path: 'prompt', name: 'PromptManage', component: genericView, meta: { title: 'Prompt 管理', module: 'prompt', permission: 'prompt:view' } },
      { path: 'mcp', name: 'McpManage', component: genericView, meta: { title: 'MCP 管理', module: 'mcp', permission: 'mcp:view' } },
      { path: 'skill', name: 'SkillManage', component: genericView, meta: { title: 'Skill / Tool 管理', module: 'skill', permission: 'skill:view' } },
      { path: 'knowledge', name: 'KnowledgeManage', component: genericView, meta: { title: '知识库管理', module: 'knowledge', permission: 'knowledge:view' } },
      { path: 'workflow', name: 'WorkflowManage', component: genericView, meta: { title: '流程编排', module: 'workflow', permission: 'workflow:view' } },
      { path: 'collaboration', name: 'CollaborationManage', component: genericView, meta: { title: '多智能体协同', module: 'collaboration', permission: 'collaboration:view' } },
      { path: 'llm', name: 'LlmProviders', component: () => import('@/views/settings/ModelProviderView.vue'), meta: { title: '大模型配置', permission: 'platform:llm' } },
      { path: 'integration', name: 'Integration', component: genericView, meta: { title: '系统集成', module: 'integration', permission: 'integration:view' } },
      { path: 'system/users', name: 'Users', component: genericView, meta: { title: '用户与权限', module: 'users', permission: 'system:users' } },
      { path: 'system/audit', name: 'Audit', component: genericView, meta: { title: '审计与日志', module: 'audit', permission: 'system:audit' } },
      { path: 'system/sandbox', name: 'SandboxMonitor', component: () => import('@/views/security/SandboxMonitorView.vue'), meta: { title: '沙箱监控', permission: 'platform:sandbox' } },
      { path: 'system/settings', name: 'Settings', component: genericView, meta: { title: '系统设置', module: 'settings', permission: 'system:settings' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
];
