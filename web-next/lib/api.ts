import type {
  IntakeResponse,
  Mode,
  RunResult,
  RunTask,
  CatalogItem,
  MediaIntake,
  StoreProfile,
  CampaignPlan,
  PosterResponse,
  PosterDocument,
} from "./types";
import { sessionScopeKey, workspaceFetch as fetch } from "./workspace";

async function read(res: Response) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined")
      window.dispatchEvent(new Event("matequill-session-expired"));
    const detail = (data as { detail?: string }).detail;
    throw new Error(
      typeof detail === "string" ? detail : `请求失败（${res.status}）`,
    );
  }
  return data;
}
export async function reviseCreation(runId: string, instruction: string): Promise<RunResult> {
  return read(await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/revise`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({instruction}),
  }));
}
export async function adoptCreation(runId: string): Promise<void> {
  await read(await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/adopt`, {method: "POST"}));
}
export async function createPoster(task: RunTask): Promise<PosterResponse> {
  const response = await read(
    await fetch("/api/posters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        brief: {
          size_preset: "portrait",
          direction: "editorial",
          background_mode: "solid",
        },
      }),
    }),
  );
  if (!response.document || response.status === "blocked")
    throw new Error(
      response.questions?.join("；") ||
        response.audit
          ?.filter((item: { severity: string }) => item.severity === "error")
          .map((item: { message: string }) => item.message)
          .join("；") ||
        "这次海报没有完成，可以调整需求后重新生成。",
    );
  return compactPoster(response);
}
function compactPoster(value: PosterResponse): PosterResponse {
  const doc = value.document;
  return {
    ...value,
    document: {
      id: doc.id,
      version: doc.version,
      width: doc.width,
      height: doc.height,
      canvas: doc.canvas,
      layers: doc.layers,
      status: doc.status,
    },
  };
}
export async function editPoster(
  poster: PosterResponse,
  layers: PosterDocument["layers"],
): Promise<PosterResponse> {
  return compactPoster(
    await read(
      await fetch(`/api/posters/${poster.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document: {
            version: poster.document.version,
            canvas: poster.document.canvas,
            layers,
          },
        }),
      }),
    ),
  );
}
export async function getPoster(id: string): Promise<PosterResponse> {
  const document: PosterDocument = await read(
    await fetch(`/api/posters/${encodeURIComponent(id)}/export?format=json`, {
      cache: "no-store",
    }),
  );
  return compactPoster({
    id,
    document,
    status: document.status,
    preview_url: `/api/posters/${id}/preview.svg?v=${document.version}`,
    exports: {
      png: `/api/posters/${id}/export?format=png`,
      svg: `/api/posters/${id}/export?format=svg`,
      json: `/api/posters/${id}/export?format=json`,
    },
  });
}

export async function intake(
  query: string,
  mode: Mode,
  answers: Record<string, string>,
): Promise<IntakeResponse> {
  return read(
    await fetch("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode, answers }),
    }),
  );
}

export class GenerationJobError extends Error {}
interface PendingGeneration {
  task: string;
  idempotency_key: string;
  job_id?: string;
}
const pendingGenerations = new Map<string, PendingGeneration>();
function generationKey() {
  return `matequill-job-v1:${sessionScopeKey()}`;
}
function savedGeneration(key: string): PendingGeneration | null {
  try {
    return JSON.parse(
      sessionStorage.getItem(key) || "null",
    ) as PendingGeneration | null;
  } catch {
    return pendingGenerations.get(key) || null;
  }
}
function saveGeneration(key: string, value: PendingGeneration) {
  pendingGenerations.set(key, value);
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Keep retry state in this tab. */
  }
}
function clearGeneration(key: string, id: string) {
  if (savedGeneration(key)?.job_id !== id) return;
  pendingGenerations.delete(key);
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* The completed result remains available. */
  }
}
export async function runTask(
  task: RunTask,
  onQueued?: (id: string) => void,
): Promise<RunResult> {
  const key = generationKey();
  const fingerprint = JSON.stringify(
    Object.fromEntries(
      Object.entries(task).sort(([a], [b]) => a.localeCompare(b)),
    ),
  );
  let pending = savedGeneration(key);
  if (pending?.task !== fingerprint) {
    const random =
      crypto.randomUUID?.() ||
      Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
        value.toString(16).padStart(2, "0"),
      ).join("");
    pending = { task: fingerprint, idempotency_key: random };
    saveGeneration(key, pending);
  }
  if (pending.job_id) {
    onQueued?.(pending.job_id);
    return waitForRunJob(pending.job_id, key);
  }
  const job: { id: string } = await read(
    await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, idempotency_key: pending.idempotency_key }),
    }),
  );
  saveGeneration(key, { ...pending, job_id: job.id });
  onQueued?.(job.id);
  return waitForRunJob(job.id, key);
}
export async function waitForRunJob(
  id: string,
  key = generationKey(),
): Promise<RunResult> {
  for (let attempt = 0; attempt < 300; attempt++) {
    const status = await read(
      await fetch(`/api/jobs/${encodeURIComponent(id)}`, {
        cache: "no-store",
      }),
    );
    if (status.status === "succeeded" && status.result) {
      clearGeneration(key, id);
      return status.result as RunResult;
    }
    if (["failed", "cancelled"].includes(status.status)) {
      clearGeneration(key, id);
      throw new GenerationJobError(
        status.error?.message || "这次创作没有完成，需求已保留，可以重新尝试。",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("这次创作仍在处理，请稍后到创作记录中查看。");
}

export async function catalog(
  mode: Mode = "local",
): Promise<{ items: CatalogItem[]; mode: string }> {
  return read(await fetch(`/api/catalog?mode=${mode}`, { cache: "no-store" }));
}

export async function uploadMedia(
  file: File,
  kind: "photo" | "audio",
): Promise<MediaIntake> {
  const body = new FormData();
  body.append("file", file);
  body.append("kind", kind);
  return read(await fetch("/api/media/intake", { method: "POST", body }));
}
export async function getRun(id: string): Promise<RunResult> {
  return read(
    await fetch(`/api/runs/${encodeURIComponent(id)}`, { cache: "no-store" }),
  );
}
export async function stores(): Promise<{ stores: StoreProfile[] }> {
  return read(await fetch("/api/stores", { cache: "no-store" }));
}
export async function saveStore(
  store: Omit<StoreProfile, "id">,
): Promise<StoreProfile> {
  return read(
    await fetch("/api/stores", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(store),
    }),
  );
}
export async function planCampaign(input: {
  brief: string;
  product: string;
  platform: string;
  store_ids: string[];
}): Promise<CampaignPlan> {
  return read(
    await fetch("/api/campaigns/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}
export interface KnowledgeDraft {
  draft_id: string;
  name: string;
  category: string;
  source_url: string;
  facts: {
    id: string;
    label: string;
    value: string;
    quote: string;
    qualifiers?: string[];
  }[];
  conditions?: string[];
}
export async function previewKnowledge(input: {
  name: string;
  category: string;
  source_url: string;
  source_text: string;
}): Promise<KnowledgeDraft> {
  return read(
    await fetch("/api/knowledge/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}
export async function confirmKnowledge(draft_id: string): Promise<unknown> {
  return read(
    await fetch("/api/knowledge/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft_id, confirmed: true }),
    }),
  );
}
export async function planEcosystem(input: {
  products: string[];
  scene: string;
  audience: string;
  platform: string;
}): Promise<{
  status: string;
  questions?: string[];
  task?: RunTask;
  capabilities?: { id: string; title: string; conditions?: string[] }[];
}> {
  return read(
    await fetch("/api/ecosystem/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}
