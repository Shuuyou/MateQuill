const API = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
type EditorPath =
  | "/api/settings"
  | "/api/knowledge/preview"
  | "/api/knowledge/confirm";

/** Closed editor routes retain the browser session; the backend authorizes roles. */
export async function forwardLocalEditor(
  request: Request,
  path: EditorPath,
  maxLength: number,
) {
  const url = new URL(request.url);
  const publicURL = new URL(
    `${url.protocol}//${request.headers.get("host") || url.host}`,
  );
  const origin = request.headers.get("origin");
  const fetchSite = request.headers.get("sec-fetch-site");
  if (
    !origin ||
    origin !== publicURL.origin ||
    (fetchSite && fetchSite !== "same-origin")
  ) {
    return Response.json(
      { detail: "请从当前工作空间提交这项操作。" },
      { status: 403 },
    );
  }
  const body = await request.text();
  if (body.length > maxLength)
    return Response.json({ detail: "内容过长，请分开保存。" }, { status: 413 });
  try {
    const response = await fetch(`${API}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
        Cookie: request.headers.get("cookie") || "",
        "X-MateQuill-Workspace":
          request.headers.get("x-matequill-workspace") || "",
      },
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
