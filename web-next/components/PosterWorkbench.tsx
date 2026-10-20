"use client";
import { useState } from "react";
import { editPoster } from "@/lib/api";
import type { PosterLayer, PosterResponse } from "@/lib/types";

export default function PosterWorkbench({
  poster,
  onChange,
  onBack,
}: {
  poster: PosterResponse;
  onChange: (poster: PosterResponse) => void;
  onBack: () => void;
}) {
  const editable = poster.document.layers.filter(
    (layer) => !layer.locked && layer.type !== "image",
  );
  const [selected, setSelected] = useState(
    editable.find((layer) => layer.type === "text")?.id ||
      editable[0]?.id ||
      "",
  );
  const [changes, setChanges] = useState<Partial<PosterLayer>>({});
  const [undo, setUndo] = useState<PosterLayer[][]>([]);
  const [redo, setRedo] = useState<PosterLayer[][]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const original = poster.document.layers.find(
    (layer) => layer.id === selected,
  );
  const layer = original ? { ...original, ...changes } : null;
  async function save(
    layers: PosterLayer[],
    direction: "save" | "undo" | "redo" = "save",
  ) {
    setBusy(true);
    setError("");
    try {
      const response = await editPoster(poster, layers);
      if (direction === "save") {
        setUndo((previous) => [...previous, poster.document.layers].slice(-20));
        setRedo([]);
      }
      if (direction === "undo") {
        setUndo((previous) => previous.slice(0, -1));
        setRedo((previous) => [...previous, poster.document.layers]);
      }
      if (direction === "redo") {
        setRedo((previous) => previous.slice(0, -1));
        setUndo((previous) => [...previous, poster.document.layers]);
      }
      onChange(response);
      setChanges({});
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "这次修改没有保存成功，画布保持原样。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="mq-poster-workbench mq-enter">
      <header className="mq-workbench-heading">
        <div>
          <p className="mq-section-label">把好内容，排成一张想发的海报</p>
          <h1>这张海报，你来定稿。</h1>
        </div>
        <button className="mq-text-button" onClick={onBack}>
          修改需求
        </button>
      </header>
      <div className="mq-poster-toolbar">
        <div>
          <button
            className="mq-text-button"
            disabled={busy || !undo.length}
            onClick={() => void save(undo[undo.length - 1], "undo")}
          >
            ↶ 撤销
          </button>
          <button
            className="mq-text-button"
            disabled={busy || !redo.length}
            onClick={() => void save(redo[redo.length - 1], "redo")}
          >
            ↷ 重做
          </button>
        </div>
        <div>
          {["png", "svg", "json"].map((format) => (
            <a key={format} href={poster.exports[format]} download>
              导出 {format === "json" ? "设计文档" : format.toUpperCase()} ↗
            </a>
          ))}
        </div>
      </div>
      <div className="mq-poster-layout">
        <div className="mq-poster-stage">
          <img
            src={poster.preview_url}
            alt="本次生成的可编辑海报"
            style={{
              aspectRatio: `${poster.document.width}/${poster.document.height}`,
            }}
          />
        </div>
        <aside className="mq-poster-properties">
          <h2>微调这一层</h2>
          <label>
            选择图层
            <select
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setChanges({});
              }}
            >
              {editable.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name || item.text?.slice(0, 22) || "设计元素"}
                </option>
              ))}
            </select>
          </label>
          {layer && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save(
                  poster.document.layers.map((item) =>
                    item.id === selected ? { ...item, ...changes } : item,
                  ),
                );
              }}
            >
              {layer.type === "text" && (
                <>
                  <label>
                    文字
                    <textarea
                      value={layer.text}
                      rows={4}
                      onChange={(event) =>
                        setChanges((previous) => ({
                          ...previous,
                          text: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <div className="mq-property-pair">
                    <label>
                      字号
                      <input
                        type="number"
                        step="any"
                        min={8}
                        max={300}
                        value={layer.font_size}
                        onChange={(event) =>
                          setChanges((previous) => ({
                            ...previous,
                            font_size: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label>
                      文字颜色
                      <input
                        type="color"
                        value={layer.color}
                        onChange={(event) =>
                          setChanges((previous) => ({
                            ...previous,
                            color: event.target.value,
                          }))
                        }
                      />
                    </label>
                  </div>
                  <label>
                    字体
                    <select
                      value={layer.font_family}
                      onChange={(event) =>
                        setChanges((previous) => ({
                          ...previous,
                          font_family: event.target.value,
                        }))
                      }
                    >
                      <option value="sans">现代黑体</option>
                      <option value="serif">人文宋体</option>
                      <option value="display">得意黑</option>
                      <option value="mono">等宽字体</option>
                    </select>
                  </label>
                </>
              )}
              {layer.type === "shape" && (
                <label>
                  填充颜色
                  <input
                    type="color"
                    value={layer.fill}
                    onChange={(event) =>
                      setChanges((previous) => ({
                        ...previous,
                        fill: event.target.value,
                      }))
                    }
                  />
                </label>
              )}
              <div className="mq-property-pair">
                <label>
                  横向位置
                  <input
                    type="number"
                    step="any"
                    min={0}
                    max={poster.document.width - layer.width}
                    value={layer.x}
                    onChange={(event) =>
                      setChanges((previous) => ({
                        ...previous,
                        x: Number(event.target.value),
                      }))
                    }
                  />
                </label>
                <label>
                  纵向位置
                  <input
                    type="number"
                    step="any"
                    min={0}
                    max={poster.document.height - layer.height}
                    value={layer.y}
                    onChange={(event) =>
                      setChanges((previous) => ({
                        ...previous,
                        y: Number(event.target.value),
                      }))
                    }
                  />
                </label>
              </div>
              <button
                className="mq-primary"
                type="submit"
                disabled={busy || !Object.keys(changes).length}
              >
                {busy ? "正在更新画布…" : "应用修改"}
                <span>↗</span>
              </button>
            </form>
          )}
          {error && (
            <p className="mq-error" role="alert">
              {error}
            </p>
          )}
        </aside>
      </div>
    </section>
  );
}
