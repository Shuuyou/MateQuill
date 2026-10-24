import { workspaceRequest } from './workspace';
export interface DesignArtboard { id: string; role: string; title: string; preview_url: string; export_urls: { png: string; svg: string; json?: string }; }
export interface DesignBundle { id: string; title: string; platform: string; version: number; artboards: DesignArtboard[]; }
export interface DesignJob { id: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'; progress?: { seq: number; ts: number; stage: string; percent: number }; result?: DesignBundle; error?: {message?: string}; }
export const getDesign = (id: string) => workspaceRequest<DesignBundle>(`/api/designs/${encodeURIComponent(id)}`);
export const getDesignJob = (id: string) => workspaceRequest<DesignJob>(`/api/jobs/${encodeURIComponent(id)}`);
export const createDesign = (source_run_id: string, platform: string | undefined, idempotency_key: string) => workspaceRequest<DesignJob>('/api/designs', { source_run_id, ...(platform ? { platform } : {}), idempotency_key });
export const reviseDesign = (id: string, instruction: string, idempotency_key: string) => workspaceRequest<DesignJob>(`/api/designs/${encodeURIComponent(id)}/revise`, { instruction, idempotency_key });
