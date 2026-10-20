const API = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
type EditorPath =
  | "/api/settings"
  | "/api/knowledge/preview"
  | "/api/knowledge/confirm";

/** A closed set of local editor operations; ordinary API requests use rewrites. */
export async function forwardLocalEditor(
  request: Request,
  path: EditorPath,
  maxLength: number,
) {
  const url = new URL(request.url);
  function loopback(host: string) {
    const value = host.toLowerCase().replace(/^\[|\]$/g, "");
    if (value === "localhost" || value === "::1") return true;
    const ipv4 = value.replace(/^::ffff:/, "");
    return isIP(ipv4) === 4 && ipv4.split(".")[0] === "127";
  }
  const publicURL = new URL(
    `${url.protocol}//${request.headers.get("host") || url.host}`,
  );
  const origin = request.headers.get("origin");
  const forwarded = request.headers.get("x-forwarded-for");
  const fetchSite = request.headers.get("sec-fetch-site");
  if (
    !loopback(publicURL.hostname) ||
    !origin ||
    origin !== publicURL.origin ||
    (fetchSite && fetchSite !== "same-origin") ||
    (forwarded && forwarded.split(",").some((peer) => !loopback(peer.trim())))
  ) {
    return Response.json(
      { detail: "这项维护操作仅支持本机同源访问。" },
      { status: 403 },
    );
  }
  const upstream = new URL(API);
  if (!loopback(upstream.hostname))
    return Response.json(
      { detail: "请通过本机工作空间维护资料与连接。" },
      { status: 403 },
    );
  const body = await request.text();
  if (body.length > maxLength)
    return Response.json({ detail: "内容过长，请分开保存。" }, { status: 413 });
  try {
    const response = await fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: upstream.origin },
      body,
      cache: "no-store",
    });
    return new Response(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "资料服务暂时没有连接成功，请保留当前内容后重试。" },
      { status: 502 },
    );
  }
}
import { isIP } from "node:net";
