"use client";

import { useState } from "react";

const FIELDS = [
  {
    name: "llm_base_url",
    label: "模型接口地址",
    placeholder: "https://api.deepseek.com",
  },
  {
    name: "llm_api_key",
    label: "模型 API Key",
    placeholder: "sk-…",
    secret: true,
  },
  { name: "llm_model", label: "模型名称", placeholder: "deepseek-flash" },
  {
    name: "image_base_url",
    label: "图像接口地址",
    placeholder: "https://api.openai-next.com/v1",
  },
  {
    name: "image_api_key",
    label: "图像 API Key",
    placeholder: "sk-…",
    secret: true,
  },
  { name: "image_model", label: "生图模型", placeholder: "gpt-image-2.5" },
  { name: "image_review_model", label: "审稿模型", placeholder: "gpt-4o" },
  { name: "ima_client_id", label: "ima Client ID", placeholder: "可选" },
  {
    name: "ima_api_key",
    label: "ima API Key",
    placeholder: "可选",
    secret: true,
  },
];

export default function SettingsForm() {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload: Record<string, string> = {};
    for (const [key, value] of form.entries()) {
      if (typeof value === "string" && value.trim())
        payload[key] = value.trim();
    }
    setBusy(true);
    setMessage("");
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail ?? `保存失败（${res.status}）`);
      setMessage("已保存到当前进程，重启后需要重新填写。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card mt-10 grid gap-5 p-7 sm:grid-cols-2"
      onSubmit={submit}
    >
      {FIELDS.map((field) => (
        <label key={field.name} className="block">
          <span className="mb-2 block text-[15px] font-medium">
            {field.label}
          </span>
          <input
            name={field.name}
            type={field.secret ? "password" : "text"}
            placeholder={field.placeholder}
            className="field"
            autoComplete="off"
          />
        </label>
      ))}
      <div className="sm:col-span-2 flex flex-wrap items-center gap-4">
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "保存中…" : "保存设置"}
        </button>
        {message && (
          <span className="text-[14px] text-secondary">{message}</span>
        )}
      </div>
    </form>
  );
}
