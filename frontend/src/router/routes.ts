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
  { key: 'chat', title: 'AI 对话', path: '/chat', icon: 'chat', permission: 'chat:view' },
  { key: 'dashboard', title: '工作台', path: '/dashboard', icon: 'dashboard', permission: 'dashboard:view' },
  {
    key: 'agents',
    title: '智能体',
    path: '/agent/manage',
    icon: 'agent',
    permission: 'agent:view',
    children: [
      { key: 'agent-manage', title: '智能体管理', path: '/agent/manage', permission: 'agent:manage' },
      { key: 'collaboration', title: '多智能体协同', path: '/collaboration', permission: 'collaboration:view' },
      { key: 'workflow', title: '流程编排', path: '/workflow', permission: 'workflow:view' },
    ],
  },
  {
    key: 'capabilities',
    title: '能力',
    path: '/llm',
    icon: 'capability',
    permission: 'platform:view',
    children: [
      { key: 'llm', title: '大模型管理', path: '/llm', permission: 'platform:llm' },
      { key: 'mcp', title: 'MCP 管理', path: '/mcp', permission: 'mcp:view' },
      { key: 'skill', title: 'Skill 管理', path: '/skill', permission: 'skill:view' },
      { key: 'tools', title: '工具注册中心', path: '/tools', permission: 'tool:view' },
      { key: 'knowledge', title: '知识库管理', path: '/knowledge', permission: 'knowledge:view' },
      { key: 'prompt', title: 'Prompt 管理', path: '/prompt', permission: 'prompt:view' },
      { key: 'external-agents', title: '外部智能体管理', path: '/external-agents', permission: 'integration:view' },
    ],
  },
  {
    key: 'resources',
    title: '资源',
    path: '/personal/agents',
    icon: 'resources',
    permission: 'resources:view',
    children: [
      { key: 'my-agents', title: '我的资源', path: '/personal/agents', permission: 'resources:personal' },
      { key: 'project-resources', title: '项目资源', path: '/project/resources', permission: 'resources:personal' },
      { key: 'hydraulic-topology', title: '水利拓扑数据', path: '/resources/topology', permission: 'resources:personal' },
      { key: 'tenant-resources', title: '租户资源', path: '/tenant/resources', permission: 'resources:public' },
      { key: 'public-agents', title: '公共资源', path: '/public/agents', permission: 'resources:public' },
      { key: 'publish-review', title: '发布审核', path: '/public/review', permission: 'publish:review' },
    ],
  },
  {
    key: 'operations',
    title: '运行',
    path: '/runs',
    icon: 'operations',
    permission: 'platform:view',
    children: [
      { key: 'runs', title: 'Agent Runs', path: '/runs', permission: 'platform:view' },
      { key: 'async-tasks', title: '异步任务', path: '/async-tasks', permission: 'platform:view' },
      { key: 'sandbox', title: '沙箱监控', path: '/system/sandbox', permission: 'platform:sandbox' },
      { key: 'artifacts', title: '成果文件', path: '/artifacts', permission: 'platform:view' },
    ],
  },
  {
    key: 'security',
    title: '安全',
    path: '/approvals',
    icon: 'security',
    permission: 'system:view',
    children: [
      { key: 'approvals', title: '待办审批', path: '/approvals', permission: 'publish:review' },
      { key: 'policies', title: '风险策略', path: '/policies', permission: 'system:settings' },
      { key: 'credentials', title: '凭据管理', path: '/credentials', permission: 'system:settings' },
      { key: 'audit', title: '审计日志', path: '/system/audit', permission: 'system:audit' },
    ],
  },
  {
    key: 'system', title: '系统', path: '/system/users', icon: 'system', permission: 'system:view',
    children: [
      { key: 'users', title: '用户与权限', path: '/system/users', permission: 'system:users' },
      { key: 'tenant-projects', title: '租户与项目', path: '/system/tenant-projects', permission: 'system:users' },
      { key: 'roles', title: '角色管理', path: '/system/roles', permission: 'system:users' },
      { key: 'sessions', title: '登录会话', path: '/system/sessions', permission: 'chat:view' },
      { key: 'integration', title: '系统集成', path: '/integration', permission: 'integration:view' },
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
      { path: 'agent/manage', name: 'AgentManage', component: () => import('@/views/agent/AgentManageView.vue'), meta: { title: '智能体管理', permission: 'agent:manage' } },
      { path: 'prompt', name: 'PromptManage', component: genericView, meta: { title: 'Prompt 管理', module: 'prompt', permission: 'prompt:view' } },
      { path: 'mcp', name: 'McpManage', component: () => import('@/views/mcp/McpManageView.vue'), meta: { title: 'MCP 管理', permission: 'mcp:view' } },
      { path: 'skill', name: 'SkillManage', component: () => import('@/views/skills/SkillManageView.vue'), meta: { title: 'Skill 管理', permission: 'skill:view' } },
      { path: 'tools', name: 'ToolManage', component: () => import('@/views/tools/ToolManageView.vue'), meta: { title: '工具注册中心', permission: 'tool:view' } },
      { path: 'knowledge', name: 'KnowledgeManage', component: genericView, meta: { title: '知识库管理', module: 'knowledge', permission: 'knowledge:view' } },
      { path: 'workflow', name: 'WorkflowManage', component: genericView, meta: { title: '流程编排', module: 'workflow', permission: 'workflow:view' } },
      { path: 'collaboration', name: 'CollaborationManage', component: genericView, meta: { title: '多智能体协同', module: 'collaboration', permission: 'collaboration:view' } },
      { path: 'llm', name: 'LlmProviders', component: () => import('@/views/settings/ModelProviderView.vue'), meta: { title: '大模型配置', permission: 'platform:llm' } },
      { path: 'integration', name: 'Integration', component: () => import('@/views/platform/IntegrationView.vue'), meta: { title: '系统集成', permission: 'integration:view' } },
      { path: 'external-agents', name: 'ExternalAgents', component: genericView, meta: { title: '外部智能体管理', module: 'integration', permission: 'integration:view' } },
      { path: 'project/resources', name: 'ProjectResources', component: resourceView, meta: { title: '项目资源', permission: 'resources:personal' } },
      { path: 'resources/topology', name: 'HydraulicTopology', component: () => import('@/views/resources/TopologyDataView.vue'), meta: { title: '水利拓扑数据', permission: 'resources:personal' } },
      { path: 'tenant/resources', name: 'TenantResources', component: resourceView, meta: { title: '租户资源', permission: 'resources:public' } },
      { path: 'runs', name: 'AgentRuns', component: () => import('@/views/runs/AgentRunListView.vue'), meta: { title: 'Agent Runs', permission: 'platform:view' } },
      { path: 'async-tasks', name: 'AsyncTasks', component: genericView, meta: { title: '异步任务', module: 'collaboration', permission: 'platform:view' } },
      { path: 'artifacts', name: 'Artifacts', component: genericView, meta: { title: '成果文件', module: 'integration', permission: 'platform:view' } },
      { path: 'approvals', name: 'Approvals', component: resourceView, meta: { title: '待办审批', permission: 'publish:review' } },
      { path: 'policies', name: 'Policies', component: genericView, meta: { title: '风险策略', module: 'settings', permission: 'system:settings' } },
      { path: 'credentials', name: 'Credentials', component: genericView, meta: { title: '凭据管理', module: 'settings', permission: 'system:settings' } },
      { path: 'system/tenant-projects', name: 'TenantProjects', component: () => import('@/views/platform/ProjectManagementView.vue'), meta: { title: '单位与项目', permission: 'system:users' } },
      { path: 'system/roles', name: 'Roles', component: () => import('@/views/platform/RoleManagementView.vue'), meta: { title: '角色管理', permission: 'system:users' } },
      { path: 'system/users', name: 'Users', component: () => import('@/views/platform/UserManagementView.vue'), meta: { title: '用户与权限', permission: 'system:users' } },
      { path: 'system/sessions', name: 'Sessions', component: () => import('@/views/security/SessionManagementView.vue'), meta: { title: '登录会话', permission: 'chat:view' } },
      { path: 'system/audit', name: 'Audit', component: () => import('@/views/security/AuditLogView.vue'), meta: { title: '审计与日志', permission: 'system:audit' } },
      { path: 'system/sandbox', name: 'SandboxMonitor', component: () => import('@/views/security/SandboxMonitorView.vue'), meta: { title: '沙箱监控', permission: 'platform:sandbox' } },
      { path: 'system/settings', name: 'Settings', component: genericView, meta: { title: '系统设置', module: 'settings', permission: 'system:settings' } },
    ],
  },
  { path: '/chat/focus', name: 'ChatFocus', component: () => import('@/views/agent/AgentConsoleView.vue'), meta: { title: 'AI 对话', permission: 'chat:view', focus: true } },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
];
