"use client";
import { useEffect, useState } from "react";
import { stores } from "@/lib/api";
import type { StoreProfile } from "@/lib/types";
import Character from "./Character";

export default function ChooseCreationStore({
  onSelect,
  onManage,
}: {
  onSelect: (store: StoreProfile) => Promise<void>;
  onManage: () => void;
}) {
  const [profiles, setProfiles] = useState<StoreProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    stores()
      .then((value) => setProfiles(value.stores))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);
  async function select(store: StoreProfile) {
    setBusy(store.id);
    setError("");
    try {
      await onSelect(store);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "暂时没有进入门店，请再试一次。",
      );
      setBusy("");
    }
  }
  return (
    <section className="mq-choose-store mq-enter">
      <header className="mq-welcome">
        <Character />
        <div>
          <h1>这次，为哪家店讲？</h1>
          <p>选好门店，带上它的顾客与日常，一起开始。</p>
        </div>
      </header>
      {loading ? (
        <p>正在取回门店资料。</p>
      ) : profiles.length ? (
        <div className="mq-creation-store-list">
          {profiles.map((store) => (
            <button
              key={store.id}
              disabled={!!busy}
              onClick={() => void select(store)}
            >
              <span>
                <strong>{store.name}</strong>
                <span>{store.audience}</span>
              </span>
              <span aria-hidden="true">{busy === store.id ? "…" : "↗"}</span>
            </button>
          ))}
        </div>
      ) : (
        <div>
          <p>先建立门店，记下它常来的顾客。</p>
          <button className="mq-primary" onClick={onManage}>
            建立门店<span>↗</span>
          </button>
        </div>
      )}
      {error && (
        <p className="mq-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
