"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createPoster,
  GenerationJobError,
  getPoster,
  getRun,
  intake,
  runTask,
  waitForRunJob,
  reviseCreation,
} from "@/lib/api";
import type {
  MediaIntake,
  PosterResponse,
  Question,
  RunResult,
  RunTask,
} from "@/lib/types";
interface SavedCreation {
  assignmentId: string;
  pendingJob: string;
  query: string;
  answers: Record<string, string>;
  media: MediaIntake[];
  result: RunResult | null;
  task: RunTask | null;
  poster: PosterResponse | null;
  history: { id: string; title: string; at: string; task?: RunTask }[];
}
const initial: SavedCreation = {
  assignmentId: "",
  pendingJob: "",
  query: "",
  answers: {},
  media: [],
  result: null,
  task: null,
  poster: null,
  history: [],
};
export function useCreation(scopeKey: string) {
  const KEY = `matequill-creation-v3:${scopeKey}`;
  const [data, setData] = useState<SavedCreation>(initial);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [phase, setPhase] = useState<"idle" | "understanding" | "generating">(
    "idle",
  );
  const [error, setError] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const active = useRef(false);
  useEffect(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (
        value &&
        typeof value.query === "string" &&
        Array.isArray(value.history)
      )
        setData({ ...initial, ...value });
    } catch {
      /* A damaged draft should not prevent a new creation. */
    }
    setHydrated(true);
  }, [KEY]);
  useEffect(() => {
    if (hydrated) {
      try {
        sessionStorage.setItem(KEY, JSON.stringify(data));
      } catch {
        /* The current draft stays available in memory. */
      }
    }
  }, [data, hydrated, KEY]);
  const update = useCallback(
    (
      patch:
        | Partial<SavedCreation>
        | ((previous: SavedCreation) => Partial<SavedCreation>),
    ) =>
      setData((previous) => ({
        ...previous,
        ...(typeof patch === "function" ? patch(previous) : patch),
      })),
    [],
  );
  const remember = (result: RunResult, task: RunTask) =>
    setData((previous) => ({
      ...previous,
      result,
      pendingJob: "",
      task,
      history: [
        {
          id: result.run_id,
          title: result.title || task.product,
          at: new Date().toISOString(),
          task,
        },
        ...previous.history.filter((item) => item.id !== result.run_id),
      ].slice(0, 12),
    }));
  async function generate(task: RunTask) {
    setPhase("generating");
    if (task.platform === "poster") {
      const poster = await createPoster(task);
      setData((previous) => ({
        ...previous,
        result: null,
        poster,
        task,
        history: [
          {
            id: `poster:${poster.id}`,
            title: task.product + " · 海报",
            at: new Date().toISOString(),
            task,
          },
          ...previous.history,
        ].slice(0, 12),
      }));
      return;
    }
    update({ poster: null });
    let result: RunResult;
    try {
      result = await runTask(task, (pendingJob) => update({ pendingJob }));
    } catch (reason) {
      if (reason instanceof GenerationJobError) update({ pendingJob: "" });
      throw reason;
    }
    remember(result, task);
  }
  async function resume() {
    if (active.current || !data.pendingJob || !data.task) return;
    active.current = true;
    setError("");
    setPhase("generating");
    try {
      remember(await waitForRunJob(data.pendingJob), data.task);
    } catch (reason) {
      if (reason instanceof GenerationJobError) update({ pendingJob: "" });
      setError(
        reason instanceof Error
          ? reason.message
          : "创作还在处理中，稍后可以继续查看。",
      );
    } finally {
      active.current = false;
      setPhase("idle");
    }
  }
  useEffect(() => {
    if (hydrated && data.pendingJob && data.task && !active.current)
      void resume();
  }, [hydrated, data.pendingJob]);
  async function resolve(extra: Record<string, string> = {}) {
    if (data.pendingJob) return resume();
    if (active.current || (!data.query.trim() && !data.media.length)) return;
    active.current = true;
    setError("");
    setPhase("understanding");
    const answers = { ...data.answers, ...extra };
    update({ answers });
    try {
      const observations = data.media
        .filter((item) => item.kind === "photo")
        .map((item) => item.observations?.caption)
        .filter(Boolean);
      const query =
        [
          data.query.trim(),
          observations.length
            ? `附加门店照片场景：${observations.join("；")}`
            : "",
        ]
          .filter(Boolean)
          .join("\n") || "请根据上传的门店素材创作一篇推广内容";
      const response =
        data.assignmentId && data.task
          ? {
              status: "ready",
              task: {
                ...data.task,
                scene: query,
                requirements: [data.task.requirements, data.query.trim()]
                  .filter(Boolean)
                  .join("\n"),
              },
              questions: [],
            }
          : await intake(query, "local", answers);
      if (response.status === "clarify") {
        setQuestions(response.questions);
        return;
      }
      setQuestions([]);
      const task: RunTask = {
        ...response.task,
        mode: "local",
        media_ids: data.media.map((item) => item.id),
      };
      update({ task });
      await generate(task);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "这次内容没有完成，需求已经保留，可以再试一次。",
      );
    } finally {
      active.current = false;
      setPhase("idle");
    }
  }
  async function direct(task: RunTask, assignmentId = "") {
    if (active.current) return;
    active.current = true;
    setError("");
    setQuestions([]);
    update({
      task,
      assignmentId,
      query: task.scene,
      result: null,
      poster: null,
    });
    try {
      await generate(task);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "这次内容没有完成，请再试一次。",
      );
    } finally {
      active.current = false;
      setPhase("idle");
    }
  }
  async function revise(instruction: string) {
    if (!data.task) return;
    if (data.result?.status === "ready" && data.task.platform !== "poster") {
      if (active.current) return;
      active.current = true;
      setError("");
      setPhase("generating");
      try {
        const result = await reviseCreation(data.result.run_id, instruction);
        remember(result, result._poster_task || data.task);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "这次修改没有完成，原稿已保留。");
      } finally {
        active.current = false;
        setPhase("idle");
      }
      return;
    }
    await direct(
      {
        ...data.task,
        requirements: [data.task.requirements, `本次修改：${instruction}`]
          .filter(Boolean)
          .join("\n"),
      },
      data.assignmentId,
    );
  }
  async function reopen(id: string) {
    setError("");
    update({ assignmentId: "" });
    try {
      if (id.startsWith("poster:"))
        update({
          poster: await getPoster(id.slice(7)),
          result: null,
          task: data.history.find((item) => item.id === id)?.task || null,
        });
      else {
        const result = await getRun(id);
        update({
          result,
          poster: null,
          task:
            result._poster_task ||
            data.history.find((item) => item.id === id)?.task ||
            null,
        });
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "这份记录暂时无法打开。",
      );
    }
  }
  function reset() {
    setQuestions([]);
    setError("");
    update({
      query: "",
      media: [],
      answers: {},
      result: null,
      poster: null,
      task: null,
      assignmentId: "",
      pendingJob: "",
    });
  }
  return {
    ...data,
    questions,
    phase,
    error,
    hydrated,
    update,
    resolve,
    direct,
    revise,
    reopen,
    reset,
    resume,
    openDraft: (result: RunResult, task: RunTask, assignmentId: string) => {
      setQuestions([]);
      setError("");
      setPhase("idle");
      update({ assignmentId, query: task.scene || "", answers: {}, media: [], poster: null });
      remember(result, task);
    },
    startAssignment: (task: RunTask, assignmentId: string) => {
      setQuestions([]);
      setError("");
      update({
        task,
        assignmentId,
        query: task.scene || "",
        answers: {},
        media: [],
        result: null,
        poster: null,
      });
    },
    clearQuestions: () => setQuestions([]),
    clearError: () => setError(""),
  };
}
