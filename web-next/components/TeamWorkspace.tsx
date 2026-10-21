"use client";
import { useEffect, useState } from "react";
import { stores } from "@/lib/api";
import type { StoreProfile } from "@/lib/types";
import {
  ROLE_NAMES,
  workspaceRequest,
  type WorkspaceRole,
} from "@/lib/workspace";
import { useWorkspace } from "./WorkspaceProvider";

interface TeamMember {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: WorkspaceRole;
  store_id: string | null;
  active: boolean;
}
interface PendingInvitation {
  id: string;
  email: string;
  role: WorkspaceRole;
  store_id: string | null;
  expires_at: number;
}
interface AuditEvent {
  id: string;
  actor_id: string;
  action: string;
  resource_kind: string;
  created_at: number;
}
const ACTION_NAMES: Record<string, string> = {
  "team.invite": "邀请同事",
  "team.invite.revoke": "撤回邀请",
  "team.member.revoke": "移除任职",
  "campaign.created": "创建活动",
  "campaign.versioned": "更新活动版本",
  "campaign.published": "下发活动",
  "campaign.withdrawn": "撤回活动",
  "campaign.granted": "发出合作活动",
  "campaign.grant_accepted": "接收合作活动",
  "campaign.grant_revoked": "结束合作授权",
  "content.submitted": "提交内容审核",
  "content.approved": "批准内容",
  "content.changes_requested": "提出修改建议",
  "publication.manually_confirmed": "确认发布记录",
  "job.queued": "提交创作任务",
  "run.generated": "完成内容创作",
  "media.uploaded": "上传门店素材",
};
export default function TeamWorkspace() {
  const { session, refresh } = useWorkspace();
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [profiles, setProfiles] = useState<StoreProfile[]>([]);
  const [adding, setAdding] = useState(false);
  const [invitation, setInvitation] = useState<{
    url: string;
    expires: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"members" | "invitations" | "audit">(
    "members",
  );
  const [invitations, setInvitations] = useState<PendingInvitation[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [usage, setUsage] = useState<{
    completed: number;
    reserved: number;
    failed: number;
    limit: number;
    day: string;
  } | null>(null);
  async function load() {
    try {
      const [team, locations, pending, audit, usage] = await Promise.all([
        workspaceRequest<{ members: TeamMember[] }>("/api/team/members"),
        stores(),
        workspaceRequest<{ invitations: PendingInvitation[] }>(
          "/api/team/invitations",
        ),
        workspaceRequest<{ events: AuditEvent[] }>("/api/team/audit"),
        workspaceRequest<{
          completed: number;
          reserved: number;
          failed: number;
          limit: number;
          day: string;
        }>("/api/usage"),
      ]);
      setMembers(team.members);
      setProfiles(locations.stores);
      setError("");
      setInvitations(pending.invitations);
      setEvents(audit.events);
      setUsage(usage);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "团队信息暂未载入。");
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function invite(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await workspaceRequest<{
        token: string;
        expires_at: number;
      }>("/api/team/invitations", {
        email: String(data.get("email")),
        role: String(data.get("role")),
        store_id: String(data.get("store_id")) || null,
      });
      setInvitation({
        url: `${window.location.origin}/?invite=${encodeURIComponent(response.token)}`,
        expires: response.expires_at,
      });
      setAdding(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "邀请没有创建成功。");
    } finally {
      setBusy(false);
    }
  }
  async function revoke(member: TeamMember) {
    if (!window.confirm(`移除 ${member.name || member.email} 的这项任职？`))
      return;
    setBusy(true);
    setError("");
    try {
      await workspaceRequest(
        `/api/team/members/${encodeURIComponent(member.id)}`,
        undefined,
        "DELETE",
      );
      await load();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "这项任职暂未移除。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="mq-team mq-enter">
      <header className="mq-page-heading">
        <p className="mq-section-label">{session.workspace?.tenant_name}</p>
        <h1>一起，把好内容做出来。</h1>
        <p>邀请同事加入，给每个人适合的门店与职责。</p>
      </header>
      <div className="mq-team-partner-code">
        <span>企业合作编号</span>
        <button
          className="mq-text-button"
          onClick={() =>
            void navigator.clipboard
              .writeText(session.workspace!.tenant_id)
              .then(() => setNotice("企业合作编号已复制，可发给活动伙伴。"))
              .catch(() => setNotice(session.workspace!.tenant_id))
          }
        >
          复制编号 ↗
        </button>
      </div>
      {usage && (
        <div className="mq-usage-summary">
          <div>
            <span>今天已完成</span>
            <strong>{usage.completed}</strong>
          </div>
          <div>
            <span>正在处理</span>
            <strong>{usage.reserved}</strong>
          </div>
          <div>
            <span>每日创作额度</span>
            <strong>{usage.limit}</strong>
          </div>
        </div>
      )}
      <nav className="mq-channel-tabs" aria-label="团队管理">
        <button
          aria-current={tab === "members" ? "page" : undefined}
          onClick={() => setTab("members")}
        >
          团队成员
        </button>
        <button
          aria-current={tab === "invitations" ? "page" : undefined}
          onClick={() => setTab("invitations")}
        >
          待加入邀请
        </button>
        <button
          aria-current={tab === "audit" ? "page" : undefined}
          onClick={() => setTab("audit")}
        >
          操作记录
        </button>
        <button
          className="mq-channel-refresh"
          disabled={busy}
          onClick={() => void load()}
        >
          刷新
        </button>
      </nav>
      {error && (
        <p role="alert" className="mq-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mq-inline-notice">
          {notice}
        </p>
      )}
      {tab === "members" && (
        <>
          <div className="mq-section-heading">
            <h2>团队成员</h2>
            <button
              className="mq-primary"
              onClick={() => {
                setAdding(!adding);
                setInvitation(null);
              }}
            >
              {adding ? "收起邀请" : "邀请同事"}
              <span>＋</span>
            </button>
          </div>
          {adding && (
            <form
              className="mq-team-invite mq-workspace-form"
              onSubmit={(event) => void invite(event)}
            >
              <label>
                同事邮箱
                <input
                  name="email"
                  required
                  type="email"
                  autoComplete="email"
                />
              </label>
              <label>
                工作职责
                <select name="role" defaultValue="salesperson">
                  {Object.entries(ROLE_NAMES)
                    .filter(
                      ([role]) =>
                        session.workspace!.roles.includes("admin") ||
                        !["admin", "operator"].includes(role),
                    )
                    .map(([role, name]) => (
                      <option key={role} value={role}>
                        {name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                任职门店
                <select name="store_id" defaultValue="">
                  <option value="">企业范围</option>
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
              <button className="mq-primary" disabled={busy} type="submit">
                {busy ? "正在创建…" : "生成邀请链接"}
                <span>↗</span>
              </button>
            </form>
          )}
          {invitation && (
            <div className="mq-invitation-card">
              <h3>把邀请发给这位同事。</h3>
              <p>
                有效期至{" "}
                {new Date(invitation.expires * 1000).toLocaleString("zh-CN")}
                ，加入后即可开始工作。
              </p>
              <div>
                <input
                  value={invitation.url}
                  readOnly
                  aria-label="团队邀请链接"
                  onFocus={(event) => event.currentTarget.select()}
                />
                <button
                  className="mq-primary"
                  onClick={() =>
                    void navigator.clipboard
                      .writeText(invitation.url)
                      .then(() => setNotice("邀请链接已复制。"))
                      .catch(() => setNotice("请选中链接复制。"))
                  }
                >
                  复制链接
                </button>
              </div>
            </div>
          )}
          <div className="mq-member-list">
            {members.map((member) => (
              <article key={member.id}>
                <span className="mq-member-avatar">
                  {(member.name || member.email).slice(0, 1)}
                </span>
                <div>
                  <h3>{member.name || member.email}</h3>
                  <p>{member.email}</p>
                </div>
                <div>
                  <strong>{ROLE_NAMES[member.role] || member.role}</strong>
                  <p>
                    {profiles.find((profile) => profile.id === member.store_id)
                      ?.name || "企业范围"}
                  </p>
                </div>
                {member.active ? (
                  <button
                    className="mq-text-button"
                    disabled={busy || member.user_id === session.user?.id}
                    onClick={() => void revoke(member)}
                  >
                    移除任职
                  </button>
                ) : (
                  <span>已移除</span>
                )}
              </article>
            ))}
          </div>
        </>
      )}
      {tab === "invitations" && (
        <div className="mq-member-list">
          {invitations.length ? (
            invitations.map((item) => (
              <article key={item.id}>
                <div>
                  <h3>{item.email}</h3>
                  <p>
                    {ROLE_NAMES[item.role]} ·{" "}
                    {profiles.find((store) => store.id === item.store_id)
                      ?.name || "企业范围"}
                  </p>
                  <p>
                    有效至{" "}
                    {new Date(item.expires_at * 1000).toLocaleString("zh-CN")}
                  </p>
                </div>
                <button
                  className="mq-text-button"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void workspaceRequest(
                      `/api/team/invitations/${item.id}`,
                      undefined,
                      "DELETE",
                    )
                      .then(load)
                      .catch((reason: Error) => setError(reason.message))
                      .finally(() => setBusy(false));
                  }}
                >
                  撤回邀请
                </button>
              </article>
            ))
          ) : (
            <p>目前没有等待加入的邀请。</p>
          )}
        </div>
      )}
      {tab === "audit" && (
        <ol className="mq-audit-list">
          {events.slice(0, 50).map((event) => (
            <li key={event.id}>
              <div>
                <strong>{ACTION_NAMES[event.action] || "工作空间操作"}</strong>
                <span>
                  {members.find((member) => member.user_id === event.actor_id)
                    ?.name || "团队成员"}
                </span>
              </div>
              <time dateTime={new Date(event.created_at * 1000).toISOString()}>
                {new Date(event.created_at * 1000).toLocaleString("zh-CN")}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
