export type Mode = "local" | "live" | "demo";
export type Platform = "xiaohongshu" | "wechat" | "moments" | "poster";

export interface QuestionOption {
  value: string;
  label: string;
}
export interface Question {
  field: string;
  label: string;
  options: QuestionOption[];
}

export interface IntakeTask {
  product: string;
  audience: string;
  scene: string;
  platform: Platform;
  stage: string;
  goal: string;
  requirements: string;
}

export interface IntakeResponse {
  status: "clarify" | "ready";
  task: IntakeTask;
  questions: Question[];
  summary: string;
}

export interface RunTask extends IntakeTask {
  mode: Mode;
  creative_connector?: string;
  creative_style?: string;
  media_ids?: string[];
  store_id?: string;
  assignment_id?: string;
  ecosystem_products?: string[];
}

export interface ContentBlock {
  text: string;
  fact_ids?: string[];
  disclosures?: string[];
}
export interface Asset {
  id: string;
  url: string;
  title?: string;
  product?: string;
  provenance?: string;
  semantic_basis?: string;
  visual_role?: string;
}
export interface AuditItem {
  severity?: "info" | "warning" | "error";
  message: string;
  code?: string;
}
export interface SourceItem {
  id: string;
  title: string;
  url?: string;
  provider?: string;
}

export interface RunResult {
  run_id: string;
  status: string;
  title?: string;
  intro?: string;
  cta?: string;
  tags?: string[];
  blocks?: ContentBlock[];
  images?: {
    asset_id: string;
    position?: string;
    caption?: string;
    block_index?: number | null;
  }[];
  assets?: Asset[];
  sources?: SourceItem[];
  audit?: AuditItem[];
  strategy?: Record<string, unknown>;
  creative_plan?: { cards?: CreativeCard[]; [key: string]: unknown };
  sales_plan?: Record<string, unknown>;
  facts?: { id: string; label: string; value: string }[];
  message?: string;
}

export interface CreativeCard {
  index?: number;
  headline?: string;
  body?: string;
  caption?: string;
  overlay_text?: string;
  asset_id?: string;
  asset_ids?: string[];
  layout?: string;
  shot?: string;
  visual_direction?: string;
}
export interface MediaIntake {
  id: string;
  kind: "photo" | "audio";
  status: string;
  preview_url?: string;
  transcript?: string;
  observations?: {
    caption?: string;
    visible_text?: string[] | string;
    product_candidates?: string[];
    scene_hints?: string[];
  };
  questions?: string[];
  message?: string;
  error?: { code: string; message: string };
}
export interface StoreProfile {
  id: string;
  name: string;
  audience: string;
  location?: string;
  core_tasks?: string[];
  voice?: string;
  materials?: string[];
}
export interface CampaignVariant {
  id: string;
  store_id: string;
  store_name: string;
  audience: string;
  angle: string;
  narrative_device: string;
  visual_direction: string;
  cta: string;
  why: string;
  task: RunTask;
}
export interface CampaignPlan {
  campaign_id: string;
  status: string;
  variants: CampaignVariant[];
  questions?: string[];
  diversity?: Record<string, unknown>;
}

export interface CatalogItem {
  id: string;
  name: string;
  system?: string;
  aliases?: string[];
  cover_url?: string;
  product?: string;
  category?: string;
  [key: string]: unknown;
}

export interface PosterLayer {
  id: string;
  type: "text" | "image" | "shape";
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  locked: boolean;
  text: string;
  font_size: number;
  color: string;
  fill: string;
  font_family: string;
  [key: string]: unknown;
}
export interface PosterDocument {
  id: string;
  version: number;
  width: number;
  height: number;
  canvas: { width: number; height: number; background: string };
  layers: PosterLayer[];
  status: string;
}
export interface PosterResponse {
  id: string;
  document: PosterDocument;
  status: string;
  preview_url: string;
  exports: Record<string, string>;
  source_run_id?: string;
}
