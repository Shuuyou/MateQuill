"use client";
import { useEffect, useState } from "react";
import { workspaceRequest } from "@/lib/workspace";
import { CHANNEL_STATUS, type ContentSubmission } from "@/lib/channel";

export default function CreationSubmission({
  assignmentId,
  runId,
  onOpenTeam,
}: {
  assignmentId: string;
  runId: string;
  onOpenTeam: () => void;
}) {
  const [note, setNote] = useState("");
  const [submission, setSubmission] = useState<ContentSubmission | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    workspaceRequest<{ submissions: ContentSubmission[] }>(
      "/api/channel/submissions",
    )
      .then((value) =>
        setSubmission(
          value.submissions.find((item) => item.run_id === runId) || null,
        ),
      )
      .catch(() => {});
  }, [runId]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setSubmission(
        await workspaceRequest<ContentSubmission>("/api/channel/submissions", {
          assignment_id: assignmentId,
          run_id: runId,
          note,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "暂时没有送达，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="mq-creation-submission">
      {submission ? (
        <>
          <div>
            <h2>{CHANNEL_STATUS[submission.status] || submission.status}</h2>
            <p>
              {submission.review_note ||
                "这一稿已经交给团队，可以在活动里查看进展。"}
            </p>
          </div>
          <button className="mq-text-button" onClick={onOpenTeam}>
            查看团队进展 ↗
          </button>
        </>
      ) : (
        <form onSubmit={(event) => void submit(event)}>
          <div>
            <h2>把这篇好内容，交给团队看看。</h2>
            <label>
              想和店长说什么
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                maxLength={1000}
              />
            </label>
          </div>
          <button className="mq-primary" disabled={busy}>
            {busy ? "正在送达…" : "提交审核"}
            <span>↗</span>
          </button>
        </form>
      )}
      {error && (
        <p role="alert" className="mq-form-error">
          {error}
        </p>
      )}
    </section>
  );
}
