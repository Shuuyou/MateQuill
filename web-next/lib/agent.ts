import { workspaceRequest } from "./workspace";

export interface AgentArtifact {
  kind: "run" | "poster" | "design";
  id: string;
  version: number;
  title: string;
  url?: string;
}
export interface AgentQuestion {
  field: string;
  label: string;
  options: { value: string; label: string }[];
}
export interface AgentMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  media_ids?: string[];
  artifact?: AgentArtifact | null;
  quotation?: { id: string } | null;
  created_at?: number;
}
export interface AgentConversation {
  id: string;
  title: string;
  version: number;
  messages: AgentMessage[];
  current_artifact: AgentArtifact | null;
  current_opportunity?: MarketingOpportunity | null;
  active_turn_id: string | null;
  pending_questions?: AgentQuestion[];
  last_turn_id?: string | null;
  updated_at?: number;
}
export interface MarketingOpportunity {
  id: string;
  title: string;
  product: string;
  rationale: string;
  audience_hypothesis: string;
  customer_task: string;
  content_angle: string;
  visual_direction: string;
  capture_steps: string[];
  experience_steps: { seconds: number; action: string; customer_value: string }[];
  materials: string[];
  capabilities_to_confirm: string[];
  next_action: string;
  status: "prepared" | "content_ready";
  experience_status: "proposed";
  sources: { source_id: string; title: string; url?: string; excerpt?: string }[];
  artifacts: AgentArtifact[];
  evidence_ids: string[];
  fact_ids: string[];
}
export interface AgentTurn {
  id: string;
  conversation_id: string;
  status: "queued" | "running" | "completed" | "needs_input" | "failed";
  message?: string;
  artifact?: AgentArtifact | null;
  quotation?: { id: string } | null;
  questions?: AgentQuestion[];
}
export interface AgentTurnInput {
  text: string;
  media_ids: string[];
  client_turn_id: string;
  reply_to_artifact?: Pick<AgentArtifact, "kind" | "id" | "version">;
  expected_version?: number;
}

const base = "/api/agent/conversations";
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const listConversations = () =>
  workspaceRequest<{ items: AgentConversation[] }>(base);
export const createConversation = (title = "") =>
  workspaceRequest<AgentConversation>(base, { title });
export const getConversation = (id: string) =>
  workspaceRequest<AgentConversation>(path(id));
export const sendAgentTurn = (id: string, input: AgentTurnInput) =>
  workspaceRequest<AgentTurn>(`${path(id)}/turns`, {
    ...input,
    // Runtime objects can include display fields even when typed as Pick.
    // Persisted retries also pass through this API boundary.
    ...(input.reply_to_artifact ? { reply_to_artifact: {
      kind: input.reply_to_artifact.kind,
      id: input.reply_to_artifact.id,
      version: input.reply_to_artifact.version,
    } } : {}),
  });
export const getAgentTurn = (id: string, turn: string) =>
  workspaceRequest<AgentTurn>(`${path(id)}/turns/${encodeURIComponent(turn)}`);
export const attachArtifact = (
  id: string,
  artifact: AgentArtifact,
  version: number,
) =>
  workspaceRequest<AgentConversation>(
    `${path(id)}/artifact`,
    {
      kind: artifact.kind,
      id: artifact.id,
      version: artifact.version,
      expected_version: version,
    },
    "PATCH",
  );

export interface MessagingConnection {
  channel: "feishu" | "dingtalk" | "qq" | "wecom" | "wechat";
  label: string;
  configured: boolean;
  connected: boolean;
  status: string;
  capabilities?: {
    text: boolean;
    photo: boolean;
    audio: boolean;
    group: boolean;
  };
}
export interface MessagingBinding {
  id: string;
  channel: MessagingConnection["channel"];
  created_at: number;
  last_seen_at?: number;
  active: boolean;
  store_id?: string;
}
export const messagingConnections = () =>
  workspaceRequest<{ items: MessagingConnection[] }>(
    "/api/messaging/connections",
  );
export const messagingBindings = () =>
  workspaceRequest<{ items: MessagingBinding[] }>("/api/messaging/bindings");
export const createBindingCode = (
  channel: MessagingConnection["channel"],
  conversation_id?: string,
) =>
  workspaceRequest<{ code: string; expires_at: number; instruction: string }>(
    "/api/messaging/bindings/code",
    { channel, ...(conversation_id ? { conversation_id } : {}) },
  );
export const revokeMessagingBinding = (id: string) =>
  workspaceRequest(
    `/api/messaging/bindings/${encodeURIComponent(id)}`,
    undefined,
    "DELETE",
  );
