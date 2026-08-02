import { defineStore } from 'pinia';
import {
  conversationsApi,
  type AgentRunInfo,
  type ConversationInfo,
  type MessageInfo,
} from '@/api/conversations';
import { getRunEvents, type RunEvent } from '@/api/runEvents';

export interface ToolActivity {
  invocation_id: string;
  display_name: string;
  tool_id: string;
  status: 'running' | 'completed' | 'failed';
  duration_ms: number | null;
  sequence: number;
}

const terminalStatuses = new Set(['completed', 'failed', 'cancelled']);
const toolEventStatuses = {
  'tool.started': 'running',
  'tool.completed': 'completed',
  'tool.failed': 'failed',
} as const;

function mergeToolActivities(activities: ToolActivity[], events: RunEvent[]): ToolActivity[] {
  const merged = new Map(activities.map((activity) => [activity.invocation_id, activity]));
  for (const event of [...events].sort((left, right) => left.sequence - right.sequence)) {
    const status = toolEventStatuses[event.event_type as keyof typeof toolEventStatuses];
    const { invocation_id: invocationId, display_name: displayName, tool_id: toolId, duration_ms: durationMs } = event.payload;
    if (!status || typeof invocationId !== 'string' || typeof displayName !== 'string' || typeof toolId !== 'string') continue;
    const previous = merged.get(invocationId);
    merged.set(invocationId, {
      invocation_id: invocationId,
      display_name: displayName,
      tool_id: toolId,
      status: previous && previous.status !== 'running' && status === 'running' ? previous.status : status,
      duration_ms: typeof durationMs === 'number' ? durationMs : previous?.duration_ms ?? null,
      sequence: Math.min(previous?.sequence ?? event.sequence, event.sequence),
    });
  }
  return [...merged.values()].sort((left, right) => left.sequence - right.sequence);
}

function wait(milliseconds: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
}

export const useConversationStore = defineStore('conversations', {
  state: () => ({
    conversations: [] as ConversationInfo[],
    activeConversationId: '',
    messages: [] as MessageInfo[],
    activeRun: null as AgentRunInfo | null,
    events: [] as RunEvent[],
    toolActivities: [] as ToolActivity[],
    loading: false,
    sending: false,
    error: '',
    pollToken: 0,
  }),
  actions: {
    async loadConversations() {
      this.loading = true;
      this.error = '';
      try {
        this.conversations = await conversationsApi.list();
        if (!this.activeConversationId && this.conversations.length) {
          await this.selectConversation(this.conversations[0].id);
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : '会话加载失败';
      } finally {
        this.loading = false;
      }
    },
    async createConversation(title: string) {
      const conversation = await conversationsApi.create(title);
      this.conversations.unshift(conversation);
      this.activeConversationId = conversation.id;
      this.messages = [];
      this.activeRun = null;
      this.events = [];
      this.toolActivities = [];
      return conversation;
    },
    async selectConversation(conversationId: string) {
      this.pollToken += 1;
      this.activeConversationId = conversationId;
      this.messages = await conversationsApi.listMessages(conversationId);
      this.activeRun = null;
      this.events = [];
      this.toolActivities = [];
    },
    startNewConversation() {
      this.pollToken += 1;
      this.activeConversationId = '';
      this.messages = [];
      this.activeRun = null;
      this.events = [];
      this.toolActivities = [];
    },
    async sendMessage(content: string, actorType: 'agent' | 'team', actorId?: string) {
      this.sending = true;
      this.error = '';
      try {
        if (!this.activeConversationId) {
          await this.createConversation(content.slice(0, 40));
        }
        const accepted = await conversationsApi.sendMessage(this.activeConversationId, {
          content,
          actor_type: actorType,
          ...(actorId ? { actor_id: actorId } : {}),
        });
        this.messages.push(accepted.message);
        this.activeRun = accepted.run;
        this.events = [];
        this.toolActivities = [];
        await this.replayEvents();
      } catch (error) {
        this.error = error instanceof Error ? error.message : '消息发送失败';
        throw error;
      } finally {
        this.sending = false;
      }
    },
    async replayEvents(pollIntervalMs = 500, maxPolls = 120) {
      if (!this.activeRun) return;
      const runId = this.activeRun.id;
      const conversationId = this.activeConversationId;
      const token = ++this.pollToken;
      let runtimeError = '';

      for (let attempt = 0; attempt < maxPolls; attempt += 1) {
        if (token !== this.pollToken || this.activeRun?.id !== runId) return;
        const latest = this.events.at(-1)?.sequence ?? 0;
        const events = await getRunEvents(runId, latest);
        if (
          token !== this.pollToken
          || this.activeRun?.id !== runId
          || this.activeConversationId !== conversationId
        ) return;
        const storedEvents = events.map((event) => (
          event.event_type in toolEventStatuses ? { ...event, payload: {} } : event
        ));
        this.events = [...new Map(
          [...this.events, ...storedEvents].map((event) => [event.sequence, event]),
        ).values()].sort((left, right) => left.sequence - right.sequence);
        this.toolActivities = mergeToolActivities(this.toolActivities, events);

        for (const event of events) {
          if (event.event_type === 'run.error' && typeof event.payload.message === 'string') {
            runtimeError = event.payload.message;
          }
          if (event.event_type === 'run.status' && typeof event.payload.status === 'string') {
            this.activeRun.status = event.payload.status;
          }
        }

        if (terminalStatuses.has(this.activeRun.status)) {
          if (this.activeRun.status === 'completed') {
            const messages = await conversationsApi.listMessages(conversationId);
            if (
              token !== this.pollToken
              || this.activeRun?.id !== runId
              || this.activeConversationId !== conversationId
            ) return;
            this.messages = messages;
          } else if (runtimeError) {
            this.error = runtimeError;
          }
          return;
        }
        if (attempt < maxPolls - 1) await wait(pollIntervalMs);
      }

      this.error = '智能体运行等待超时，请稍后查看会话结果';
    },
  },
});