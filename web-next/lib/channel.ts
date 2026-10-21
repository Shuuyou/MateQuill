import type { RunResult, RunTask } from "./types";
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
}
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
