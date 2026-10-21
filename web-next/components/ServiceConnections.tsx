"use client";
import { useEffect, useState } from "react";
import { workspaceRequest } from "@/lib/workspace";

export default function ServiceConnections() {
  const [value, setValue] = useState<Record<string, boolean> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    workspaceRequest<{ settings: Record<string, boolean> }>("/api/status")
      .then((response) => setValue(response.settings))
      .catch((reason: Error) => setError(reason.message));
  }, []);
  return (
    <section className="mq-service-connections">
      {error && (
        <p role="alert" className="mq-error">
          {error}
        </p>
      )}
      {value ? (
        <dl>
          {[
            ["llm_configured", "内容创作"],
            ["image_configured", "图像服务"],
            ["asr_configured", "语音识别"],
          ].map(([key, name]) => (
            <div key={key}>
              <dt>{name}</dt>
              <dd>{value[key] ? "已连接" : "待配置"}</dd>
            </div>
          ))}
        </dl>
      ) : (
        !error && <p>正在读取服务连接。</p>
      )}
    </section>
  );
}
