import { request } from './client';

export interface ConversationInfo {
  id: string;
  project_id: string;
  owner_id: string;
  title: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageInfo {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  created_at: string;
}

export interface AgentRunInfo {
  id: string;
  conversation_id: string;
  trigger_message_id: string;
  actor_type: 'agent' | 'team';
  actor_id: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface MessageAccepted {
  message: MessageInfo;
  run: AgentRunInfo;
}

export const conversationsApi = {
  list: () => request<ConversationInfo[]>('/conversations'),
  listMessages: (conversationId: string) =>
    request<MessageInfo[]>(`/conversations/${encodeURIComponent(conversationId)}/messages`),
  create: (title: string) =>
    request<ConversationInfo>('/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  sendMessage: (
    conversationId: string,
    body: { content: string; actor_type: 'agent' | 'team'; actor_id: string },
  ) =>
    request<MessageAccepted>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
};
