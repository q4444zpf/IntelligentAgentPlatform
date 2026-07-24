<template>
  <a-card class="section-card" :body-style="{ padding: 0 }">
    <div class="chat-panel">
      <aside class="chat-list">
        <div class="chat-list-heading">
          <div>
            <a-typography-title :level="5">智能体工作区</a-typography-title>
            <a-typography-text type="secondary">选择运行身份与能力范围</a-typography-text>
          </div>
          <a-tag color="blue">沙箱</a-tag>
        </div>

        <a-list :data-source="agents" :split="false">
          <template #renderItem="{ item }">
            <a-list-item class="agent-option" :class="{ active: selectedAgent.id === item.id }" @click="selectedAgent = item">
              <a-list-item-meta :title="item.name" :description="item.desc" />
              <a-tag :color="item.color">{{ item.scope }}</a-tag>
            </a-list-item>
          </template>
        </a-list>

        <a-divider />
        <div class="side-section-title">当前能力</div>
        <a-space wrap>
          <a-tag v-for="tool in selectedAgent.tools" :key="tool">{{ tool }}</a-tag>
        </a-space>
        <a-alert class="chat-safety-alert" type="info" show-icon message="工具调用受控" description="高风险动作需要人工确认，执行记录会写入审计日志。" />
      </aside>

      <section class="chat-room">
        <div class="chat-room-header">
          <div>
            <a-typography-title :level="4">{{ selectedAgent.name }}</a-typography-title>
            <a-typography-text type="secondary">{{ selectedAgent.desc }}</a-typography-text>
          </div>
          <a-button @click="clearConversation">清空会话</a-button>
        </div>

        <div ref="messageList" class="message-list" aria-live="polite">
          <div v-for="message in messages" :key="message.id" class="message" :class="message.role">
            <div v-if="message.role === 'agent'" class="message-label">{{ selectedAgent.name }}</div>
            {{ message.content }}
          </div>
          <a-spin v-if="sending" class="message agent" tip="正在分析任务..." />
        </div>

        <a-divider />
        <a-space wrap class="prompt-chips">
          <a-button v-for="prompt in suggestedPrompts" :key="prompt" size="small" @click="input = prompt">{{ prompt }}</a-button>
        </a-space>
        <a-textarea v-model:value="input" :rows="4" :maxlength="2000" show-count :disabled="sending" placeholder="输入任务，Enter 发送，Shift + Enter 换行" @keydown.enter.exact.prevent="sendMessage" />
        <div class="composer-footer">
          <a-typography-text type="secondary">{{ input.length }}/2000 · 当前为演示运行时</a-typography-text>
          <a-button type="primary" :loading="sending" :disabled="!input.trim()" @click="sendMessage">发送任务</a-button>
        </div>
      </section>
    </div>
  </a-card>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';

interface AgentOption {
  id: string;
  name: string;
  desc: string;
  scope: string;
  color: string;
  tools: string[];
}

interface ChatMessage {
  id: number;
  role: 'user' | 'agent';
  content: string;
}

const agents: AgentOption[] = [
  { id: 'knowledge', name: '知识库问答智能体', desc: '文档检索、引用溯源、多轮问答', scope: '公用', color: 'green', tools: ['知识库检索', '引用溯源'] },
  { id: 'tool', name: '工具调用智能体', desc: '参数补全、Tool 调用、结果解释', scope: '个人', color: 'blue', tools: ['HTTP API', 'MCP', '人工确认'] },
  { id: 'workflow', name: '流程执行智能体', desc: '触发流程、处理人工确认节点', scope: '系统', color: 'purple', tools: ['流程编排', '审批节点'] },
];

const suggestedPrompts = ['检查个人 MCP 发布条件', '生成水库调度报告提纲', '测试当前工具权限'];
const selectedAgent = ref(agents[0]);
const input = ref('');
const sending = ref(false);
const messageList = ref<HTMLElement>();
const messages = ref<ChatMessage[]>([
  { id: 1, role: 'agent', content: '你好，我可以协助检索知识、调用受控工具或执行已发布流程。请描述你的任务。' },
]);

watch(messages, async () => {
  await nextTick();
  if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight;
}, { deep: true });

async function sendMessage() {
  const content = input.value.trim();
  if (!content || sending.value) return;
  messages.value.push({ id: Date.now(), role: 'user', content });
  input.value = '';
  sending.value = true;
  await new Promise((resolve) => window.setTimeout(resolve, 650));
  messages.value.push({
    id: Date.now() + 1,
    role: 'agent',
    content: `已收到任务。我会以“${selectedAgent.value.name}”身份先校验权限和输入参数，再进入沙箱执行。当前演示运行时已记录本次对话，接入真实模型后可继续执行后续步骤。`,
  });
  sending.value = false;
}

function clearConversation() {
  messages.value = [{ id: Date.now(), role: 'agent', content: '会话已清空。请重新描述需要处理的任务。' }];
}
</script>
