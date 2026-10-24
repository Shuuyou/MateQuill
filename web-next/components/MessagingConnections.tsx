"use client";
import { useEffect, useRef, useState } from "react";
import {
  createBindingCode,
  messagingBindings,
  messagingConnections,
  revokeMessagingBinding,
  type MessagingBinding,
  type MessagingConnection,
} from "@/lib/agent";
import { useWorkspace } from "./WorkspaceProvider";

export default function MessagingConnections({
  onClose,
}: {
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { scopeKey } = useWorkspace();
  const [connections, setConnections] = useState<MessagingConnection[]>([]);
  const [bindings, setBindings] = useState<MessagingBinding[]>([]);
  const [selected, setSelected] = useState<MessagingConnection["channel"] | "">(
    "",
  );
  const [code, setCode] = useState<{
    code: string;
    expires_at: number;
    instruction: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  async function load() {
    const [channels, accounts] = await Promise.all([
      messagingConnections(),
      messagingBindings(),
    ]);
    setConnections(channels.items);
    setBindings(accounts.items.filter((item) => item.active));
    setSelected(
      (value) =>
        value ||
        channels.items.find((item) => item.configured && item.connected)
          ?.channel ||
        "",
    );
  }
  useEffect(() => {
    dialog.current?.showModal();
    let live = true;
    void load()
      .catch((reason: Error) => {
        if (live) setError(reason.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    if (!code) return;
    const timer = setInterval(() => {
      if (Date.now() / 1000 >= code.expires_at) {
        clearInterval(timer);
        return;
      }
      void messagingBindings()
        .then((response) =>
          setBindings(response.items.filter((item) => item.active)),
        )
        .catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [code]);
  async function makeCode() {
    if (!selected) return;
    setBusy(true);
    setError("");
    setCopied(false);
    try {
      const conversation = sessionStorage.getItem(
        `matequill-agent:${scopeKey}:active`,
      );
      const value = await createBindingCode(
        selected,
        conversation || undefined,
      );
      setCode(value);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "这次没有取得连接码，请再试一次。",
      );
    } finally {
      setBusy(false);
    }
  }
  const available = connections.filter(
    (item) => item.configured && item.status !== "not_enabled",
  );
  return (
    <dialog
      ref={dialog}
      className="mq-messaging-dialog"
      aria-labelledby="messaging-title"
      onCancel={onClose}
    >
      <header>
        <h2 id="messaging-title">在熟悉的聊天里，接着创作。</h2>
        <button className="mq-text-button" onClick={onClose}>
          关闭
        </button>
      </header>
      <p>照片、修改意见和正在做的作品，都可以接着用。</p>
      {loading ? (
        <p role="status">正在读取你的聊天入口。</p>
      ) : (
        <>
          {available.length > 0 ? (
            <section className="mq-messaging-connect">
              <label>
                选择聊天入口
                <select
                  value={selected}
                  disabled={busy}
                  onChange={(event) => {
                    setSelected(
                      event.target.value as MessagingConnection["channel"],
                    );
                    setCode(null);
                  }}
                >
                  <option value="">选择一个入口</option>
                  {available.map((item) => (
                    <option key={item.channel} value={item.channel}>
                      {item.label}
                      {item.connected ? "" : " · 等待连接"}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="mq-primary"
                onClick={() => void makeCode()}
                disabled={
                  busy ||
                  !connections.find((item) => item.channel === selected)
                    ?.connected
                }
              >
                {busy ? "正在准备…" : "把这次创作带过去"}
                <span>↗</span>
              </button>
              {code && (
                <div className="mq-messaging-code">
                  <p>{code.instruction}</p>
                  <code>{code.code}</code>
                  <button
                    className="mq-text-button"
                    onClick={() => {
                      void navigator.clipboard
                        .writeText(`绑定 ${code.code}`)
                        .then(() => setCopied(true))
                        .catch(() => setError("可以选中连接码复制。"));
                    }}
                  >
                    {copied ? "已复制" : "复制绑定消息"}
                  </button>
                  <p>
                    连接码有效至{" "}
                    {new Date(code.expires_at * 1000).toLocaleTimeString(
                      "zh-CN",
                      { hour: "2-digit", minute: "2-digit" },
                    )}
                    。
                  </p>
                </div>
              )}
            </section>
          ) : (
            !error && (
              <p className="mq-messaging-empty">
                团队尚未开通聊天入口。你可以先在网页里完成这次创作。
              </p>
            )
          )}
          {bindings.length > 0 && (
            <section className="mq-messaging-bound">
              <h3>你已关联的入口</h3>
              {bindings.map((item) => (
                <div key={item.id}>
                  <span>
                    {connections.find(
                      (channel) => channel.channel === item.channel,
                    )?.label || item.channel}
                  </span>
                  <button
                    className="mq-text-button"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true);
                      void revokeMessagingBinding(item.id)
                        .then(load)
                        .catch((reason: Error) => setError(reason.message))
                        .finally(() => setBusy(false));
                    }}
                  >
                    解除关联
                  </button>
                </div>
              ))}
            </section>
          )}
        </>
      )}
      {error && (
        <p className="mq-form-error" role="alert">
          {error}
        </p>
      )}
    </dialog>
  );
}
