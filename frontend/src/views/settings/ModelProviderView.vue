<template>
  <div class="model-page">
    <a-alert v-if="loadError" type="error" show-icon class="backend-alert" message="模型服务暂时不可用" :description="loadError">
      <template #action><a-button size="small" :loading="loadingProviders" @click="retryLoadProviders">重新连接</a-button></template>
    </a-alert>
    <a-card class="section-card provider-panel" :bordered="true" :loading="loadingProviders && !providers.length">
      <div class="panel-header">
        <div>
          <h2>模型供应商</h2>
          <p>配置云端、本地及自定义模型服务，供智能体、工作流和知识库统一使用</p>
        </div>

        <div class="header-actions">
          <button class="default-model-pill" type="button" @click="defaultModelOpen = true">
            <span class="status-dot" />
            <span class="pill-label">默认大模型</span>
            <strong>{{ defaultProvider }} / {{ defaultModel }}</strong>
            <span class="pill-edit">编辑</span>
          </button>
          <a-input
            v-model:value="searchQuery"
            allow-clear
            class="provider-search"
            placeholder="搜索模型供应商"
          >
            <template #prefix><SearchOutlined /></template>
          </a-input>
          <a-tooltip title="刷新供应商状态">
            <a-button aria-label="刷新供应商状态" :loading="refreshing" @click="refreshProviders">
              <template #icon><ReloadOutlined /></template>
            </a-button>
          </a-tooltip>
          <a-button type="primary" @click="openAddProvider">
            <template #icon><PlusOutlined /></template>
            添加供应商
          </a-button>
        </div>
      </div>

      <a-tabs v-model:activeKey="activeTab" class="provider-tabs">
        <a-tab-pane key="cloud" :tab="`云端模型 (${cloudProviders.length + availableCloud.length})`" />
        <a-tab-pane key="local" :tab="`本地与自定义 (${localProviders.length + availableLocal.length})`" />
      </a-tabs>

      <section class="configured-section">
        <div class="section-title-row">
          <div class="section-title"><span class="section-dot online" />已配置</div>
          <span class="section-meta">{{ currentConfigured.length }} 个服务在线</span>
        </div>

        <div v-if="filteredConfigured.length" class="provider-grid">
          <article v-for="provider in filteredConfigured" :key="provider.id" class="provider-card">
            <div class="provider-card-header">
              <div class="provider-identity">
                <span class="provider-logo" :style="{ background: provider.logoBg, color: provider.logoColor }">
                  {{ provider.logo }}
                </span>
                <div>
                  <div class="provider-name">{{ provider.name }}</div>
                  <div class="provider-state"><span class="status-dot" />已连接</div>
                </div>
              </div>
              <a-tag v-if="provider.free" color="green">FREE</a-tag>
            </div>

            <div v-if="provider.variants?.length" class="variant-switch">
              <button
                v-for="variant in provider.variants"
                :key="variant"
                type="button"
                :class="{ active: provider.activeVariant === variant }"
                @click="provider.activeVariant = variant"
              >
                <span class="variant-dot" />{{ variant }}
              </button>
            </div>

            <dl class="provider-details">
              <div>
                <dt>Endpoint</dt>
                <dd class="mono" :title="provider.endpoint">{{ provider.endpoint }}</dd>
              </div>
              <div>
                <dt>API Key</dt>
                <dd class="mono key-row"><span>{{ provider.apiKey }}</span><a @click="openConfig(provider)">更换</a></dd>
              </div>
              <div>
                <dt>Models</dt>
                <dd>{{ provider.models.length }} 个模型可用</dd>
              </div>
            </dl>

            <div class="card-actions">
              <a-button type="text" @click="openModels(provider)">模型</a-button>
              <a-button type="text" @click="openConfig(provider)">设置</a-button>
              <a-popconfirm title="确定停用该供应商吗？" ok-text="停用" cancel-text="取消" @confirm="disableProvider(provider)">
                <a-button type="text" danger>停用</a-button>
              </a-popconfirm>
            </div>
          </article>
        </div>
        <a-empty v-else :description="searchQuery ? '没有匹配的已配置供应商' : '暂无已配置供应商'" />
      </section>

      <section v-if="filteredAvailable.length" class="available-section">
        <div class="section-title-row">
          <div class="section-title"><span class="section-dot" />可配置</div>
          <span class="section-meta">选择供应商并填写连接凭证</span>
        </div>
        <div class="available-grid">
          <button v-for="provider in filteredAvailable" :key="provider.id" type="button" class="available-item" @click="openConfig(provider, true)">
            <span class="provider-logo small" :style="{ background: provider.logoBg, color: provider.logoColor }">{{ provider.logo }}</span>
            <span>{{ provider.name }}</span>
            <a-tag v-if="provider.free" color="green">FREE</a-tag>
            <span class="configure-link">配置 →</span>
          </button>
        </div>
      </section>
    </a-card>

    <a-modal v-model:open="defaultModelOpen" title="设置默认大模型" ok-text="保存" cancel-text="取消" @ok="saveDefaultModel">
      <a-alert message="默认模型将用于未单独指定模型的智能体和工作流。" type="info" show-icon class="modal-alert" />
      <a-form layout="vertical">
        <a-form-item label="模型供应商" required>
          <a-select v-model:value="defaultDraft.provider" :options="defaultProviderOptions" @change="onDefaultProviderChange" />
        </a-form-item>
        <a-form-item label="模型" required>
          <a-select v-model:value="defaultDraft.model" show-search :options="defaultModelOptions" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="customProviderOpen"
      title="添加自定义提供商"
      width="650px"
      ok-text="创建"
      cancel-text="取消"
      :confirm-loading="creatingProvider"
      :ok-button-props="{ disabled: !canCreateProvider }"
      class="custom-provider-modal"
      @ok="createCustomProvider"
      @cancel="resetCustomProviderForm"
    >
      <a-form layout="vertical" class="custom-provider-form">
        <a-form-item
          label="提供商 ID"
          required
          :validate-status="customFormTouched.id && customFormErrors.id ? 'error' : undefined"
          :help="customFormTouched.id && customFormErrors.id ? customFormErrors.id : '小写字母、数字、连字符、下划线，创建后不可更改。'"
        >
          <a-input
            v-model:value="customProviderForm.id"
            placeholder="例如 openai, google, anthropic"
            maxlength="64"
            autocomplete="off"
            @blur="customFormTouched.id = true"
          />
        </a-form-item>
        <a-form-item
          label="显示名称"
          required
          :validate-status="customFormTouched.name && customFormErrors.name ? 'error' : undefined"
          :help="customFormTouched.name ? customFormErrors.name : undefined"
        >
          <a-input
            v-model:value="customProviderForm.name"
            placeholder="例如 OpenAI, Google Gemini"
            maxlength="60"
            autocomplete="off"
            @blur="customFormTouched.name = true"
          />
        </a-form-item>
        <a-form-item
          label="默认 Base URL"
          :validate-status="customFormTouched.baseUrl && customFormErrors.baseUrl ? 'error' : undefined"
          :help="customFormTouched.baseUrl ? customFormErrors.baseUrl : '可选，后续仍可在供应商设置中修改。'"
        >
          <a-input
            v-model:value="customProviderForm.baseUrl"
            placeholder="例如 https://api.example.com"
            autocomplete="url"
            @blur="customFormTouched.baseUrl = true"
          />
        </a-form-item>
        <a-form-item label="API Key 前缀（可选）" extra="用于提示用户输入正确格式的密钥，不会作为真实密钥保存。">
          <a-input v-model:value="customProviderForm.apiKeyPrefix" placeholder="例如 sk-" maxlength="24" autocomplete="off" />
        </a-form-item>
        <a-form-item label="协议" required extra="为当前配置选择提供商 API 协议。">
          <a-select v-model:value="customProviderForm.protocol" :options="customProtocolOptions" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:open="configOpen" :title="`配置 ${editingProvider?.name || '供应商'}`" width="800px" class="provider-config-modal">
      <a-form layout="vertical" class="provider-config-form">
        <a-form-item v-if="editingProvider?.isCustom" label="协议" extra="自定义提供商的协议在创建后不可修改。">
          <a-select v-model:value="configForm.protocol" disabled :options="protocolOptions" />
        </a-form-item>
        <a-form-item label="Base URL" required :validate-status="configErrors.endpoint ? 'error' : undefined" :help="configErrors.endpoint || '模型服务的 API 基础地址。'">
          <a-input v-model:value="configForm.endpoint" placeholder="https://api.example.com/v1" :disabled="editingProvider?.freezeUrl" @input="markConfigDirty" />
        </a-form-item>
        <a-form-item :label="configForm.authMode === 'auth_token' ? 'Auth Token' : 'API Key'" :validate-status="configErrors.apiKey ? 'error' : undefined" :help="configErrors.apiKey || (!editingProvider?.requireApiKey ? '该供应商不需要 API Key，可直接保存。' : undefined)">
          <a-input-password v-model:value="configForm.apiKey" :disabled="!editingProvider?.requireApiKey" :placeholder="!editingProvider?.requireApiKey ? '无需 API Key' : editingProvider?.apiKey ? '留空以保持当前密钥' : apiKeyPlaceholder" @input="markConfigDirty" />
        </a-form-item>

        <div class="advanced-config-section">
          <button type="button" class="advanced-config-toggle" @click="advancedConfigOpen = !advancedConfigOpen">
            <span><DownOutlined v-if="advancedConfigOpen" /><RightOutlined v-else />高级配置</span>
          </button>
          <template v-if="advancedConfigOpen">
            <a-form-item v-if="editingProvider?.protocol === 'AnthropicChatModel'" label="认证方式">
              <a-radio-group v-model:value="configForm.authMode" @change="markConfigDirty"><a-radio value="api_key">API Key</a-radio><a-radio value="auth_token">Auth Token</a-radio></a-radio-group>
            </a-form-item>
            <a-form-item label="自定义请求头" extra="请求调用模型服务时附加的 HTTP Header。">
              <div class="custom-headers-section">
                <div v-for="(header, index) in configForm.headers" :key="index" class="custom-header-row">
                  <a-input v-model:value="header.key" placeholder="Header 名称" @input="markConfigDirty" />
                  <a-input v-model:value="header.value" placeholder="Header 值" @input="markConfigDirty" />
                  <a-button type="text" danger aria-label="删除请求头" @click="removeConfigHeader(index)"><CloseOutlined /></a-button>
                </div>
                <button type="button" class="add-header-button" @click="addConfigHeader">+ 添加请求头</button>
              </div>
            </a-form-item>
            <a-form-item label="生成参数配置" :validate-status="configErrors.generateConfig ? 'error' : undefined" :help="configErrors.generateConfig || 'JSON 对象会展开并传入模型生成请求。'">
              <a-textarea v-model:value="configForm.generateConfig" :rows="8" class="json-textarea" placeholder="{&#10;  &quot;temperature&quot;: 0.7&#10;}" @input="markConfigDirty" />
            </a-form-item>
          </template>
        </div>
      </a-form>
      <template #footer>
        <div class="provider-modal-footer">
          <div>
            <a-button v-if="editingProvider?.configured" danger @click="editingProvider && disableProvider(editingProvider)">撤销授权</a-button>
            <a-button v-if="editingProvider?.supportConnectionCheck" :loading="testingProvider" @click="testProviderConnection"><template #icon><ApiOutlined /></template>测试连接</a-button>
          </div>
          <div><a-button @click="configOpen = false">取消</a-button><a-button type="primary" :loading="saving" :disabled="!configDirty || !!configErrors.endpoint || !!configErrors.apiKey || !!configErrors.generateConfig" @click="saveProvider">保存</a-button></div>
        </div>
      </template>
    </a-modal>

    <a-modal
      v-model:open="modelsOpen"
      :title="`${editingProvider?.name || ''} — 模型管理`"
      width="960px"
      :footer="null"
      class="model-manage-modal"
    >
      <a-input v-model:value="modelSearch" allow-clear placeholder="搜索模型..." class="model-search">
        <template #prefix><SearchOutlined /></template>
      </a-input>

      <div class="model-list">
        <a-empty v-if="!filteredModels.length" description="没有匹配的模型" />
        <div v-for="model in filteredModels" :key="model.id" class="model-row" :class="{ expanded: expandedModelId === model.id }">
          <div class="model-row-summary">
            <div class="model-row-info">
              <strong>{{ model.name }}</strong>
              <span>{{ model.id }}</span>
            </div>
            <div class="model-row-actions">
              <a-tag><FileTextOutlined /> {{ model.type }}</a-tag>
              <a-tag v-if="model.supportsMultimodal" color="purple">多模态</a-tag>
              <a-tag :color="model.builtin ? 'green' : 'blue'"><DatabaseOutlined /> {{ model.builtin ? '内置' : '自定义' }}</a-tag>
              <a-divider type="vertical" />
              <a-tooltip title="测试连接">
                <a-button type="text" size="small" aria-label="测试连接" :loading="testingModelId === model.id" @click="testModel(model)"><ApiOutlined /></a-button>
              </a-tooltip>
              <a-tooltip title="测试多模态能力">
                <a-button type="text" size="small" aria-label="测试多模态能力" :loading="probingModelId === model.id" @click="probeMultimodal(model)"><PictureOutlined /></a-button>
              </a-tooltip>
              <a-tooltip title="模型设置">
                <a-button type="text" size="small" aria-label="模型设置" @click="toggleModel(model)"><SettingOutlined /></a-button>
              </a-tooltip>
              <a-popconfirm v-if="!model.builtin" title="确定删除该自定义模型吗？" ok-text="删除" cancel-text="取消" @confirm="removeModel(model)">
                <a-tooltip title="删除模型"><a-button type="text" danger size="small" aria-label="删除模型" :loading="removingModelId === model.id"><DeleteOutlined /></a-button></a-tooltip>
              </a-popconfirm>
            </div>
          </div>

          <div v-if="expandedModelId === model.id" class="model-config-panel">
            <div class="model-field-grid">
              <div class="model-field">
                <label>最大输出 Tokens</label>
                <a-input-number v-model:value="model.maxTokens" :min="1" :max="1000000" style="width: 100%" />
                <span>每次响应的最大输出 token 数</span>
              </div>
              <div class="model-field">
                <label>最大上下文长度</label>
                <a-input-number v-model:value="model.contextWindow" :min="1000" :max="10000000" style="width: 100%" />
                <span>模型上下文窗口大小，控制上下文压缩阈值（≥1000）</span>
              </div>
            </div>
            <div class="switch-field">
              <div>
                <label>转发推理内容</label>
                <span>将推理内容（reasoning contents）回传至后续对话轮次</span>
              </div>
              <a-switch v-model:checked="model.forwardReasoning" />
            </div>
            <div class="advanced-editor">
              <p>使用 JSON 格式表示的该模型专属生成参数配置项，会被展开传入到生成请求中。优先级高于提供商级别的进阶配置。</p>
              <a-textarea v-model:value="model.extraConfig" :rows="7" class="json-textarea" spellcheck="false" />
              <div class="editor-actions">
                <span v-if="modelJsonError === model.id" class="json-error">JSON 格式不正确，请检查后保存</span>
                <a-button type="primary" size="small" @click="saveModelConfig(model)">保存</a-button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="addingModel" class="model-add-form">
        <div class="model-field-grid">
          <div class="model-field"><label>模型 ID <em>*</em></label><a-input v-model:value="newModel.id" placeholder="例如 deepseek-chat" /></div>
          <div class="model-field"><label>显示名称</label><a-input v-model:value="newModel.name" placeholder="例如 DeepSeek Chat" /></div>
        </div>
        <div class="model-field-grid add-second-row">
          <div class="model-field"><label>模型类型</label><a-select v-model:value="newModel.type" :options="modelTypeOptions" /></div>
          <div class="add-form-actions"><a-button @click="addingModel = false">取消</a-button><a-button type="primary" :disabled="!newModel.id.trim()" @click="confirmAddModel">添加模型</a-button></div>
        </div>
      </div>
      <div v-else class="model-modal-actions">
        <a-button v-if="editingProvider?.supportModelDiscovery" block :loading="discoveringModels" @click="discoverModels"><template #icon><SearchOutlined /></template>自动发现模型</a-button>
        <a-button block type="dashed" @click="openAddModel"><template #icon><PlusOutlined /></template>添加模型</a-button>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { ApiOutlined, CloseOutlined, DatabaseOutlined, DeleteOutlined, DownOutlined, FileTextOutlined, PictureOutlined, PlusOutlined, ReloadOutlined, RightOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons-vue';
import { modelProviderApi, type ApiModel, type ApiProvider } from '@/api/modelProviders';

interface ModelItem { id: string; name: string; type: string; enabled: boolean; builtin?: boolean; maxTokens?: number; contextWindow?: number; forwardReasoning?: boolean; extraConfig?: string; supportsImage?: boolean; supportsVideo?: boolean; supportsMultimodal?: boolean; probeSource?: string }
interface ProviderCard {
  id: string; name: string; kind: 'cloud' | 'local'; endpoint: string; apiKey: string;
  logo: string; logoBg: string; logoColor: string; free?: boolean; configured: boolean;
  protocol: string; isCustom: boolean; requireApiKey: boolean; enabled: boolean; freezeUrl: boolean; supportConnectionCheck: boolean;
  apiKeyPrefixes: string[]; generateKwargs: Record<string, unknown>; customHeaders: Record<string, string>; authMode: 'api_key' | 'auth_token'; supportModelDiscovery: boolean;
  variants?: string[]; activeVariant?: string; models: ModelItem[];
}

const providers = reactive<ProviderCard[]>([]);
const logoPalette: Record<string, [string, string, string]> = {
  deepseek: ['D', '#edf4ff', '#3366e8'], dashscope: ['千', '#fff3e8', '#f07824'], zhipu: ['智', '#eef3ff', '#315bdd'],
  volcengine: ['火', '#fff0ee', '#e5483f'], moonshot: ['M', '#f1f1f4', '#17171c'], openrouter: ['OR', '#f3efff', '#6d47c9'],
  openai: ['AI', '#eaf8f2', '#17845b'], anthropic: ['A', '#fff3eb', '#d5672f'], ollama: ['O', '#f0f2f5', '#172033'], vllm: ['V', '#f0edff', '#6546c7'],
  gemini: ['G', '#eef5ff', '#4285f4'], minimax: ['M', '#fff0f3', '#e94b71'], 'mimo-tokenplan': ['Mi', '#f4f4f5', '#18181b'],
  modelscope: ['MS', '#f1efff', '#6546c7'], siliconflow: ['SF', '#f4efff', '#7c3aed'], 'azure-openai': ['Az', '#e8f4ff', '#1677b9'],
};

function mapModel(item: ApiModel): ModelItem {
  return { id: item.id, name: item.name, type: item.type, enabled: item.enabled, builtin: item.builtin, maxTokens: item.max_tokens, contextWindow: item.context_window, forwardReasoning: item.forward_reasoning, extraConfig: JSON.stringify(item.extra_config || {}, null, 2), supportsImage: item.supports_image, supportsVideo: item.supports_video, supportsMultimodal: item.supports_multimodal, probeSource: item.probe_source };
}
function mapProvider(item: ApiProvider): ProviderCard {
  const palette = logoPalette[item.id] || [item.name.slice(0, 2).toUpperCase(), '#edf5fb', '#1e6bb8'];
  return { id: item.id, name: item.name, kind: item.kind, endpoint: item.base_url, apiKey: item.masked_api_key || (item.require_api_key ? '' : '无需 API Key'), logo: palette[0], logoBg: palette[1], logoColor: palette[2], free: item.is_free_tier, configured: item.configured, protocol: item.protocol, isCustom: item.is_custom, requireApiKey: item.require_api_key, enabled: item.enabled, freezeUrl: item.freeze_url, supportConnectionCheck: item.support_connection_check, supportModelDiscovery: item.support_model_discovery, apiKeyPrefixes: item.api_key_prefixes || [], generateKwargs: item.generate_kwargs || {}, customHeaders: item.custom_headers || {}, authMode: item.auth_mode || 'api_key', activeVariant: item.provider_variant, models: item.models.map(mapModel) };
}
function replaceProvider(item: ApiProvider) {
  const mapped = mapProvider(item); const index = providers.findIndex((provider) => provider.id === item.id);
  if (index >= 0) providers.splice(index, 1, mapped); else providers.push(mapped);
  if (editingProvider.value?.id === item.id) editingProvider.value = mapped;
}
async function loadProviders() {
  loadingProviders.value = true;
  try {
    const [items, active] = await Promise.all([modelProviderApi.list(), modelProviderApi.getActive()]);
    providers.splice(0, providers.length, ...items.map(mapProvider));
    loadError.value = '';
    if (active.provider_id && active.model) {
      const provider = providers.find((item) => item.id === active.provider_id); const model = provider?.models.find((item) => item.id === active.model);
      defaultDraft.provider = active.provider_id; defaultDraft.model = active.model; defaultProvider.value = provider?.name || active.provider_id; defaultModel.value = model?.name || active.model;
    } else { defaultProvider.value = '未设置'; defaultModel.value = '—'; }
  } catch (error) {
    loadError.value = `${error instanceof Error ? error.message : '无法连接模型服务'}。请确认后端服务已在 8000 端口启动。`;
    defaultProvider.value = '服务未连接'; defaultModel.value = '—';
    throw error;
  } finally { loadingProviders.value = false; }
}

const activeTab = ref<'cloud' | 'local'>('cloud');
const searchQuery = ref('');
const loadingProviders = ref(true);
const loadError = ref('');
const refreshing = ref(false);
const saving = ref(false);
const testingProvider = ref(false);
const configDirty = ref(false);
const advancedConfigOpen = ref(false);
const creatingProvider = ref(false);
const defaultModelOpen = ref(false);
const customProviderOpen = ref(false);
const configOpen = ref(false);
const modelsOpen = ref(false);
const modelSearch = ref('');
const expandedModelId = ref('');
const testingModelId = ref('');
const probingModelId = ref('');
const modelJsonError = ref('');
const addingModel = ref(false);
const discoveringModels = ref(false);
const removingModelId = ref('');
const isNewProvider = ref(false);
const editingProvider = ref<ProviderCard>();
const defaultProvider = ref('加载中');
const defaultModel = ref('—');
const defaultDraft = reactive({ provider: '', model: '' });
const configForm = reactive<{ name: string; endpoint: string; apiKey: string; protocol: string; authMode: 'api_key' | 'auth_token'; headers: Array<{ key: string; value: string }>; generateConfig: string }>({ name: '', endpoint: '', apiKey: '', protocol: 'OpenAIChatModel', authMode: 'api_key', headers: [], generateConfig: '' });
const customProviderForm = reactive({ id: '', name: '', baseUrl: '', apiKeyPrefix: '', protocol: 'OpenAIChatModel' });
const newModel = reactive({ id: '', name: '', type: '文本' });
const customFormTouched = reactive({ id: false, name: false, baseUrl: false });

const cloudProviders = computed(() => providers.filter((p) => p.kind === 'cloud' && p.configured));
const localProviders = computed(() => providers.filter((p) => p.kind === 'local' && p.configured));
const availableCloud = computed(() => providers.filter((p) => p.kind === 'cloud' && !p.configured));
const availableLocal = computed(() => providers.filter((p) => p.kind === 'local' && !p.configured));
const currentConfigured = computed(() => activeTab.value === 'cloud' ? cloudProviders.value : localProviders.value);
const currentAvailable = computed(() => activeTab.value === 'cloud' ? availableCloud.value : availableLocal.value);
const matchesSearch = (p: ProviderCard) => !searchQuery.value.trim() || `${p.name} ${p.endpoint}`.toLowerCase().includes(searchQuery.value.trim().toLowerCase());
const filteredConfigured = computed(() => currentConfigured.value.filter(matchesSearch));
const filteredAvailable = computed(() => currentAvailable.value.filter(matchesSearch));
const defaultProviderOptions = computed(() => providers.filter((p) => p.configured && p.models.length).map((p) => ({ label: p.name, value: p.id })));
const defaultModelOptions = computed(() => providers.find((p) => p.id === defaultDraft.provider)?.models.filter((m) => m.enabled).map((m) => ({ label: `${m.name} (${m.id})`, value: m.id })) || []);
const protocolOptions = [{ label: 'OpenAI 兼容（Chat Completions）', value: 'OpenAIChatModel' }, { label: 'OpenAI 兼容（Response API）', value: 'OpenAIResponseModel' }, { label: 'Anthropic（Messages API）', value: 'AnthropicChatModel' }];
const customProtocolOptions = [
  { label: 'OpenAI 兼容（Chat Completions）', value: 'OpenAIChatModel' },
  { label: 'OpenAI 兼容（Response API）', value: 'OpenAIResponseModel' },
  { label: 'Anthropic（Messages API）', value: 'AnthropicChatModel' },
];
const customFormErrors = computed(() => {
  const id = customProviderForm.id.trim();
  const name = customProviderForm.name.trim();
  const baseUrl = customProviderForm.baseUrl.trim();
  return {
    id: !id ? '请输入提供商 ID' : !/^[a-z][a-z0-9_-]{0,63}$/.test(id) ? '必须以小写字母开头，仅可包含小写字母、数字、连字符和下划线' : providers.some((p) => p.id === id) ? '该提供商 ID 已存在' : '',
    name: !name ? '请输入显示名称' : '',
    baseUrl: baseUrl && !/^https?:\/\/[^\s]+$/i.test(baseUrl) ? '请输入以 http:// 或 https:// 开头的有效地址' : '',
  };
});
const canCreateProvider = computed(() => !customFormErrors.value.id && !customFormErrors.value.name && !customFormErrors.value.baseUrl && !!customProviderForm.protocol);
const apiKeyPlaceholder = computed(() => editingProvider.value?.apiKeyPrefixes.length ? `例如 ${editingProvider.value.apiKeyPrefixes.join('、')}...` : '输入 API Key');
const configErrors = computed(() => {
  const endpoint = configForm.endpoint.trim(); let generateConfig = '';
  if (configForm.generateConfig.trim()) { try { const parsed = JSON.parse(configForm.generateConfig); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') generateConfig = '生成参数必须是 JSON 对象'; } catch { generateConfig = '请输入有效的 JSON 对象'; } }
  const apiKey = configForm.apiKey && editingProvider.value?.apiKeyPrefixes.length && !editingProvider.value.apiKeyPrefixes.some((prefix) => configForm.apiKey.startsWith(prefix)) ? `API Key 应以 ${editingProvider.value.apiKeyPrefixes.join(' 或 ')} 开头` : '';
  return { endpoint: !endpoint ? '请输入 Base URL' : !/^https?:\/\/[^\s]+$/i.test(endpoint) ? '请输入有效的 HTTP 或 HTTPS 地址' : '', apiKey, generateConfig };
});
const filteredModels = computed(() => {
  const query = modelSearch.value.trim().toLowerCase();
  return (editingProvider.value?.models || []).filter((model) => !query || `${model.name} ${model.id} ${model.type}`.toLowerCase().includes(query));
});
const modelTypeOptions = [{ label: '文本', value: '文本' }, { label: '推理', value: '推理' }, { label: '多模态', value: '多模态' }, { label: 'Embedding', value: 'Embedding' }, { label: 'Rerank', value: 'Rerank' }];

function onDefaultProviderChange() { defaultDraft.model = defaultModelOptions.value[0]?.value || ''; }
async function saveDefaultModel() {
  const p = providers.find((item) => item.id === defaultDraft.provider);
  const m = p?.models.find((item) => item.id === defaultDraft.model);
  if (!p || !m) return;
  try { await modelProviderApi.setActive(p.id, m.id); defaultProvider.value = p.name; defaultModel.value = m.name; defaultModelOpen.value = false; message.success('默认大模型已更新'); }
  catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); }
}
function openConfig(provider: ProviderCard, isNew = false) {
  editingProvider.value = provider; isNewProvider.value = isNew;
  Object.assign(configForm, { name: provider.name, endpoint: provider.endpoint, apiKey: '', protocol: provider.protocol, authMode: provider.authMode, headers: Object.entries(provider.customHeaders).map(([key, value]) => ({ key, value })), generateConfig: Object.keys(provider.generateKwargs).length ? JSON.stringify(provider.generateKwargs, null, 2) : '' });
  configDirty.value = !provider.enabled; advancedConfigOpen.value = false; configOpen.value = true;
}
function markConfigDirty() { configDirty.value = true; }
function addConfigHeader() { configForm.headers.push({ key: '', value: '' }); configDirty.value = true; }
function removeConfigHeader(index: number) { configForm.headers.splice(index, 1); configDirty.value = true; }
function getConfigPayload() {
  const custom_headers = Object.fromEntries(configForm.headers.filter((item) => item.key.trim()).map((item) => [item.key.trim(), item.value]));
  const generate_kwargs = configForm.generateConfig.trim() ? JSON.parse(configForm.generateConfig) as Record<string, unknown> : {};
  return { name: configForm.name, base_url: configForm.endpoint.trim(), api_key: configForm.apiKey || undefined, protocol: configForm.protocol, generate_kwargs, custom_headers, auth_mode: configForm.authMode, enabled: true };
}
async function testProviderConnection() {
  if (!editingProvider.value || configErrors.value.endpoint || configErrors.value.apiKey || configErrors.value.generateConfig) return;
  testingProvider.value = true;
  try { const result = await modelProviderApi.testProvider(editingProvider.value.id, getConfigPayload()); (result.success ? message.success : message.error)(`${result.message}${result.latency_ms ? `，耗时 ${result.latency_ms}ms` : ''}`); }
  catch (error) { message.error(error instanceof Error ? error.message : '连接测试失败'); }
  finally { testingProvider.value = false; }
}
function openAddProvider() {
  resetCustomProviderForm();
  customProviderOpen.value = true;
}
function resetCustomProviderForm() {
  Object.assign(customProviderForm, { id: '', name: '', baseUrl: '', apiKeyPrefix: '', protocol: 'OpenAIChatModel' });
  Object.assign(customFormTouched, { id: false, name: false, baseUrl: false });
}
async function createCustomProvider() {
  Object.assign(customFormTouched, { id: true, name: true, baseUrl: true });
  if (!canCreateProvider.value) return;
  creatingProvider.value = true;
  try {
    const item = await modelProviderApi.create({ id: customProviderForm.id.trim(), name: customProviderForm.name.trim(), default_base_url: customProviderForm.baseUrl.trim(), api_key_prefix: customProviderForm.apiKeyPrefix.trim(), protocol: customProviderForm.protocol });
    replaceProvider(item); activeTab.value = 'local'; customProviderOpen.value = false; message.success(`提供商“${item.name}”已创建`); resetCustomProviderForm();
  } catch (error) { message.error(error instanceof Error ? error.message : '创建失败'); }
  finally { creatingProvider.value = false; }
}
async function saveProvider() {
  if (!configForm.name.trim() || !configForm.endpoint.trim()) { message.warning('请填写供应商名称和 API Base URL'); return; }
  saving.value = true;
  try {
    if (!editingProvider.value) return;
    const item = await modelProviderApi.configure(editingProvider.value.id, getConfigPayload());
    replaceProvider(item); configDirty.value = false; configOpen.value = false; message.success('供应商配置已保存');
  } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); }
  finally { saving.value = false; }
}
function normalizeModel(model: ModelItem) {
  model.builtin ??= !model.id.startsWith('custom-');
  model.maxTokens ??= model.type === '推理' ? 32768 : 8192;
  model.contextWindow ??= model.id.includes('deepseek') ? 131072 : 128000;
  model.forwardReasoning ??= true;
  model.extraConfig ??= '{\n  "extra_body": {\n    "enable_thinking": false\n  }\n}';
}
function openModels(provider: ProviderCard) {
  editingProvider.value = provider;
  provider.models.forEach(normalizeModel);
  modelSearch.value = ''; expandedModelId.value = ''; addingModel.value = false; modelsOpen.value = true;
}
function toggleModel(model: ModelItem) { expandedModelId.value = expandedModelId.value === model.id ? '' : model.id; }
async function testModel(model: ModelItem) {
  testingModelId.value = model.id;
  try { if (!editingProvider.value) return; const result = await modelProviderApi.testModel(editingProvider.value.id, model.id); (result.success ? message.success : message.error)(`${model.name}：${result.message}${result.latency_ms ? `，耗时 ${result.latency_ms}ms` : ''}`); }
  catch (error) { message.error(error instanceof Error ? error.message : '测试失败'); }
  finally { testingModelId.value = ''; }
}
async function probeMultimodal(model: ModelItem) {
  if (!editingProvider.value) return;
  probingModelId.value = model.id;
  try {
    const providerId = editingProvider.value.id;
    const result = await modelProviderApi.probeMultimodal(providerId, model.id);
    const latest = await modelProviderApi.list(); const provider = latest.find((item) => item.id === providerId); if (provider) replaceProvider(provider);
    if (result.supports_multimodal) message.success(`${model.name} 支持多模态：${result.image_message}`);
    else message.warning(`${model.name} 未检测到多模态能力：${result.image_message}`);
  } catch (error) { message.error(error instanceof Error ? error.message : '多模态检测失败'); }
  finally { probingModelId.value = ''; }
}
async function saveModelConfig(model: ModelItem) {
  let extraConfig: Record<string, unknown>; try { extraConfig = JSON.parse(model.extraConfig || '{}'); } catch { modelJsonError.value = model.id; return; }
  try { if (!editingProvider.value) return; const item = await modelProviderApi.configureModel(editingProvider.value.id, model.id, { max_tokens: model.maxTokens || 8192, context_window: model.contextWindow || 128000, forward_reasoning: model.forwardReasoning ?? true, extra_config: extraConfig, enabled: model.enabled }); replaceProvider(item); modelJsonError.value = ''; message.success(`${model.name} 配置已保存`); }
  catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); }
}
function openAddModel() { Object.assign(newModel, { id: '', name: '', type: '文本' }); addingModel.value = true; }
async function confirmAddModel() {
  if (!editingProvider.value || !newModel.id.trim()) return;
  if (editingProvider.value.models.some((model) => model.id === newModel.id.trim())) { message.warning('模型 ID 已存在'); return; }
  try { const item = await modelProviderApi.addModel(editingProvider.value.id, { id: newModel.id.trim(), name: newModel.name.trim() || undefined, type: newModel.type }); replaceProvider(item); addingModel.value = false; message.success('模型已添加'); }
  catch (error) { message.error(error instanceof Error ? error.message : '添加失败'); }
}
async function removeModel(model: ModelItem) {
  if (!editingProvider.value || model.builtin) return;
  removingModelId.value = model.id;
  try { const item = await modelProviderApi.removeModel(editingProvider.value.id, model.id); replaceProvider(item); expandedModelId.value = ''; message.success(`模型“${model.name}”已删除`); }
  catch (error) { message.error(error instanceof Error ? error.message : '删除模型失败'); }
  finally { removingModelId.value = ''; }
}
async function discoverModels() {
  if (!editingProvider.value) return;
  discoveringModels.value = true;
  try {
    const providerId = editingProvider.value.id;
    const result = await modelProviderApi.discoverModels(providerId, true);
    const latest = await modelProviderApi.list(); const provider = latest.find((item) => item.id === providerId); if (provider) replaceProvider(provider);
    message.success(result.added_count ? `发现 ${result.discovered_count} 个模型，新增 ${result.added_count} 个` : `已发现 ${result.discovered_count} 个模型，没有新增模型`);
  } catch (error) { message.error(error instanceof Error ? error.message : '模型发现失败'); }
  finally { discoveringModels.value = false; }
}
async function disableProvider(provider: ProviderCard) {
  try { const item = await modelProviderApi.configure(provider.id, { name: provider.name, base_url: provider.endpoint, protocol: provider.protocol, generate_kwargs: provider.generateKwargs, custom_headers: provider.customHeaders, auth_mode: provider.authMode, enabled: false }); replaceProvider(item); configOpen.value = false; message.success(`${provider.name} 已停用`); }
  catch (error) { message.error(error instanceof Error ? error.message : '停用失败'); }
}
async function refreshProviders() { refreshing.value = true; try { await loadProviders(); message.success('供应商状态已刷新'); } catch (error) { message.error(error instanceof Error ? error.message : '刷新失败'); } finally { refreshing.value = false; } }
async function retryLoadProviders() { try { await loadProviders(); message.success('模型服务连接成功'); } catch { /* 页面保留错误提示 */ } }
onMounted(() => { loadProviders().catch(() => undefined); });
</script>

<style scoped>
.model-page { min-height: calc(100vh - 104px); }
.backend-alert { margin-bottom: 16px; border: 1px solid #ffccc7; }
.provider-panel :deep(.ant-card-body) { padding: 0; }
.panel-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding: 20px 24px 14px; }
.panel-header h2 { margin: 0; color: #172033; font-size: 18px; line-height: 28px; }
.panel-header p { margin: 3px 0 0; color: #667085; font-size: 13px; }
.header-actions { display: flex; align-items: center; gap: 8px; }
.default-model-pill { display: flex; align-items: center; gap: 7px; height: 32px; max-width: 330px; padding: 0 11px; color: #475467; background: #f7fafc; border: 1px solid #dce6f0; border-radius: 16px; cursor: pointer; }
.default-model-pill strong { overflow: hidden; max-width: 140px; color: #172033; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.pill-label, .pill-edit { font-size: 12px; white-space: nowrap; }
.pill-edit { color: #1e6bb8; }
.status-dot { display: inline-block; flex: 0 0 auto; width: 7px; height: 7px; background: #21a366; border-radius: 50%; box-shadow: 0 0 0 3px rgb(33 163 102 / 12%); }
.provider-search { width: 220px; }
.provider-tabs { padding: 0 24px; }
.provider-tabs :deep(.ant-tabs-nav) { margin-bottom: 18px; }
.configured-section, .available-section { margin: 0 24px 20px; padding: 18px; background: #f8fafc; border: 1px solid #e2eaf2; border-radius: 8px; }
.available-section { background: #fbfcfe; border-style: dashed; }
.section-title-row { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
.section-title { display: flex; align-items: center; gap: 8px; color: #344054; font-size: 14px; font-weight: 700; }
.section-dot { width: 8px; height: 8px; background: #98a2b3; border-radius: 50%; }
.section-dot.online { background: #21a366; }
.section-meta { color: #98a2b3; font-size: 12px; }
.provider-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.provider-card { display: flex; min-width: 0; flex-direction: column; padding: 16px 16px 0; overflow: hidden; background: #fff; border: 1px solid #dfe7f0; border-radius: 8px; box-shadow: 0 2px 7px rgb(30 62 98 / 4%); transition: border-color 160ms, box-shadow 160ms; }
.provider-card:hover { border-color: #b8d2eb; box-shadow: 0 6px 16px rgb(30 62 98 / 8%); }
.provider-card-header, .provider-identity { display: flex; align-items: center; }
.provider-card-header { justify-content: space-between; gap: 12px; }
.provider-identity { min-width: 0; gap: 10px; }
.provider-logo { display: grid; width: 36px; height: 36px; flex: 0 0 auto; place-items: center; font-size: 13px; font-weight: 800; border-radius: 9px; }
.provider-logo.small { width: 28px; height: 28px; font-size: 11px; border-radius: 7px; }
.provider-name { overflow: hidden; color: #172033; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.provider-state { display: flex; align-items: center; gap: 6px; margin-top: 2px; color: #667085; font-size: 11px; }
.provider-state .status-dot { width: 6px; height: 6px; box-shadow: none; }
.variant-switch { display: flex; gap: 3px; margin-top: 14px; padding: 3px; overflow-x: auto; background: #f2f5f8; border-radius: 6px; }
.variant-switch button { display: flex; align-items: center; gap: 5px; padding: 5px 8px; color: #667085; font-size: 11px; white-space: nowrap; background: transparent; border: 0; border-radius: 4px; cursor: pointer; }
.variant-switch button.active { color: #174f86; font-weight: 600; background: #fff; box-shadow: 0 1px 3px rgb(18 55 93 / 10%); }
.variant-dot { width: 5px; height: 5px; background: #21a366; border-radius: 50%; }
.provider-details { display: grid; gap: 10px; margin: 15px 0; }
.provider-details div { min-width: 0; }
.provider-details dt { margin-bottom: 4px; color: #98a2b3; font-size: 11px; }
.provider-details dd { margin: 0; color: #344054; font-size: 12px; }
.provider-details .mono { padding: 7px 9px; overflow: hidden; font-family: Consolas, monospace; background: #f7f9fc; border: 1px solid #edf1f5; border-radius: 5px; text-overflow: ellipsis; white-space: nowrap; }
.key-row { display: flex; justify-content: space-between; gap: 8px; }
.key-row a { font-family: inherit; }
.card-actions { display: grid; grid-template-columns: repeat(3, 1fr); margin: auto -16px 0; border-top: 1px solid #edf1f5; }
.card-actions :deep(.ant-btn) { height: 38px; border-radius: 0; }
.card-actions :deep(.ant-btn + .ant-btn) { border-left: 1px solid #edf1f5; }
.available-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.available-item { display: flex; min-width: 0; align-items: center; gap: 10px; padding: 10px 14px; color: #344054; text-align: left; background: #fafafa; border: 1px solid #f0f2f5; border-radius: 8px; cursor: pointer; transition: all 150ms ease; }
.available-item:hover { background: #fff; border-color: #1e6bb8; box-shadow: 0 2px 8px rgb(30 107 184 / 10%); }
.available-item > span:nth-child(2) { overflow: hidden; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.configure-link { margin-left: auto; color: #1e6bb8; font-size: 12px; }
.provider-config-form { padding-top: 4px; }
.advanced-config-section { overflow: hidden; border-top: 1px solid #edf1f5; }
.advanced-config-toggle { width: 100%; padding: 14px 0; color: #344054; text-align: left; background: transparent; border: 0; cursor: pointer; }
.advanced-config-toggle span { display: inline-flex; align-items: center; gap: 7px; font-weight: 600; }
.custom-headers-section { display: grid; gap: 8px; }
.custom-header-row { display: grid; grid-template-columns: 1fr 1.4fr 34px; gap: 8px; }
.add-header-button { padding: 7px; color: #1e6bb8; background: #f7fafc; border: 1px dashed #b9cee2; border-radius: 6px; cursor: pointer; }
.provider-modal-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.provider-modal-footer > div { display: flex; gap: 8px; }
.provider-config-modal :deep(.ant-modal-footer) { margin-top: 8px; padding-top: 14px; border-top: 1px solid #edf1f5; }
.modal-alert { margin-bottom: 18px; }
.custom-provider-form { margin-top: 8px; }
.custom-provider-form :deep(.ant-form-item) { margin-bottom: 22px; }
.custom-provider-form :deep(.ant-form-item-label > label) { color: #344054; font-weight: 600; }
.custom-provider-form :deep(.ant-form-item-extra), .custom-provider-form :deep(.ant-form-item-explain) { margin-top: 5px; color: #667085; font-size: 12px; }
.custom-provider-form :deep(.ant-form-item-explain-error) { color: #ff4d4f; }
.custom-provider-modal :deep(.ant-modal-header) { padding-bottom: 15px; border-bottom: 1px solid #edf1f5; }
.custom-provider-modal :deep(.ant-modal-footer) { padding-top: 15px; border-top: 1px solid #edf1f5; }
.model-search { margin: 4px 0 10px; }
.model-list { max-height: 560px; overflow-y: auto; border: 1px solid #e4e9ef; border-radius: 8px; }
.model-list :deep(.ant-empty) { margin: 38px 0; }
.model-row { background: #fff; border-bottom: 1px solid #edf1f5; }
.model-row:last-child { border-bottom: 0; }
.model-row.expanded { background: #fbfcfe; }
.model-row-summary { display: flex; min-height: 78px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 20px; }
.model-row-info { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.model-row-info strong { overflow: hidden; color: #172033; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.model-row-info span { color: #98a2b3; font-family: Consolas, monospace; font-size: 12px; }
.model-row-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 4px; }
.model-row-actions :deep(.ant-tag) { display: inline-flex; align-items: center; gap: 5px; margin-inline-end: 2px; }
.model-config-panel { padding: 12px 20px 20px; background: #fbfcfe; border-top: 1px solid #edf1f5; }
.model-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.model-field { display: flex; flex-direction: column; gap: 6px; }
.model-field label, .switch-field label { color: #344054; font-size: 13px; font-weight: 600; }
.model-field label em { color: #ff4d4f; font-style: normal; }
.model-field > span, .switch-field span, .advanced-editor p { color: #98a2b3; font-size: 12px; }
.switch-field { display: flex; align-items: center; justify-content: space-between; margin: 22px 0 14px; }
.switch-field > div { display: flex; flex-direction: column; gap: 5px; }
.advanced-editor p { margin: 0 0 8px; line-height: 1.6; }
.json-textarea { font-family: Consolas, 'SFMono-Regular', monospace; font-size: 12px; line-height: 1.7; background: #f8fafc; }
.editor-actions { display: flex; min-height: 32px; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 8px; }
.json-error { margin-right: auto; color: #ff4d4f; font-size: 12px; }
.add-model-button { height: 40px; margin-top: 14px; }
.model-modal-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
.model-modal-actions :deep(.ant-btn) { height: 40px; }
.model-add-form { margin-top: 14px; padding: 16px; background: #fafbfd; border: 1px dashed #cfd9e4; border-radius: 8px; }
.add-second-row { align-items: end; margin-top: 14px; }
.add-form-actions { display: flex; align-items: end; justify-content: flex-end; gap: 8px; }
.model-manage-modal :deep(.ant-modal-body) { padding-top: 12px; }
@media (max-width: 1500px) { .provider-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .available-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } .default-model-pill { max-width: 260px; } }
</style>
