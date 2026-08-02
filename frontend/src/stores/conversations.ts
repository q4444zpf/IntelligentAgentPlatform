import { defineStore } from 'pinia';
import {
  conversationsApi,
  type AgentRunInfo,
  type ConversationInfo,
  type MessageInfo,
} from '@/api/conversations';
import { getRunEvents, type RunEvent } from '@/api/runEvents';

const terminalStatuses = new Set(['completed', 'failed', 'cancelled']);

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
      return conversation;
    },
    async selectConversation(conversationId: string) {
      this.pollToken += 1;
      this.activeConversationId = conversationId;
      this.messages = await conversationsApi.listMessages(conversationId);
      this.activeRun = null;
      this.events = [];
    },
    startNewConversation() {
      this.pollToken += 1;
      this.activeConversationId = '';
      this.messages = [];
      this.activeRun = null;
      this.events = [];
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
        this.events.push(...events);

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