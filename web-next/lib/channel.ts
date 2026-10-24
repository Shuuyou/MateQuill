import type { RunResult, RunTask } from "./types";
import type { MarketingOpportunity } from "./agent";
export interface StoreOpportunity {
  assignment_id: string;
  store_id?: string;
  store_name?: string;
  status: string;
  available?: boolean;
  opportunity: MarketingOpportunity | null;
  prepared_at?: number | null;
  adopted_at?: number | null;
  diversity?: { selection_reason?: string; method?: string; embedding_state?: string } | null;
  alternatives?: (MarketingOpportunity & { candidate_id: string; selected: boolean })[];
}
export interface CampaignOpportunities {
  campaign_id: string;
  campaign_version: number;
  stores: StoreOpportunity[];
}
export interface ChannelCampaign {
  id: string;
  title: string;
  brief: string;
  product: string;
  platform: string;
  ecosystem_products: string[];
  version: number;
  status: string;
  starts_at?: string;
  ends_at?: string;
  created_at: string;
  assignments_count: number;
  store_ids?: string[];
  required_copy?: string;
  source_grant_id?: string;
}
export interface StoreAssignment {
  id: string;
  campaign_id: string;
  title: string;
  store_id: string;
  store_name: string;
  status: string;
  campaign_version: number;
  task: RunTask;
  submission_id?: string;
  updated_at: string;
  available?: boolean;
  unavailable_reason?: string;
  publisher_label?: string;
  publisher_kind?: "brand" | "regional";
  is_simulation?: boolean;
  source_refs?: {
    title: string;
    url: string;
    published_at?: string | null;
    excerpt?: string;
    claims?: (string | Record<string, unknown>)[];
  }[];
}
export interface PreparedAssignment {
  status: "ready";
  task: RunTask;
  opportunity: {
    title: string;
    reason?: string;
    summary?: string;
    reasons?: string[];
    [key: string]: unknown;
  };
  sources?: { title: string; url?: string }[];
}
export type PrepareAssignmentResponse = PreparedAssignment | {
  status: "unavailable";
  reasons?: string[];
  message?: string;
};
export interface ContentSubmission {
  id: string;
  assignment_id: string;
  title: string;
  store_name: string;
  run_id: string;
  revision: number;
  status: string;
  note: string;
  review_note: string;
  content_snapshot: RunResult;
  created_at: string;
  submitted_by?: string;
  image_previews?: { asset_id: string; caption: string; url: string }[];
  publication?: {
    source: string;
    url: string;
    note: string;
    created_at: string;
  };
}
export const CHANNEL_STATUS: Record<string, string> = {
  draft: "活动草稿",
  active: "进行中",
  completed: "已完成",
  withdrawn: "已撤回",
  assigned: "待领取",
  pending: "待领取",
  accepted: "待创作",
  creating: "创作中",
  in_progress: "创作中",
  submitted: "等待审核",
  changes_requested: "请修改",
  approved: "可发布",
  published: "已确认发布",
};
