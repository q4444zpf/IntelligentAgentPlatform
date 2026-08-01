import { defineStore } from 'pinia';
import {
  conversationsApi,
  type AgentRunInfo,
  type ConversationInfo,
  type MessageInfo,
} from '@/api/conversations';
import { getRunEvents, type RunEvent } from '@/api/runEvents';

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
      this.activeConversationId = conversationId;
      this.messages = await conversationsApi.listMessages(conversationId);
      this.activeRun = null;
      this.events = [];
    },
    startNewConversation() {
      this.activeConversationId = '';
      this.messages = [];
      this.activeRun = null;
      this.events = [];
    },
    async sendMessage(content: string, actorType: 'agent' | 'team', actorId: string) {
      this.sending = true;
      this.error = '';
      try {
        if (!this.activeConversationId) {
          await this.createConversation(content.slice(0, 40));
        }
        const accepted = await conversationsApi.sendMessage(this.activeConversationId, {
          content,
          actor_type: actorType,
          actor_id: actorId,
        });
        this.messages.push(accepted.message);
        this.activeRun = accepted.run;
        await this.replayEvents();
      } catch (error) {
        this.error = error instanceof Error ? error.message : '消息发送失败';
        throw error;
      } finally {
        this.sending = false;
      }
    },
    async replayEvents() {
      if (!this.activeRun) return;
      const latest = this.events.at(-1)?.sequence ?? 0;
      const events = await getRunEvents(this.activeRun.id, latest);
      this.events.push(...events);
      for (const event of events) {
        if (event.event_type === 'run.status' && typeof event.payload.status === 'string') {
          this.activeRun.status = event.payload.status;
        }
      }
    },
  },
});
