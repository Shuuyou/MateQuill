import { NextResponse, type NextRequest } from "next/server";
import { canonicalRedirect } from "./lib/server/publicOrigin";

export function proxy(request: NextRequest) {
  try {
    const destination = canonicalRedirect(
      request.url,
      request.headers.get("host"),
      request.headers.get("x-forwarded-proto"),
      process.env.MATEQUILL_PUBLIC_URL,
    );
    if (!destination) return NextResponse.next();
    const response = NextResponse.redirect(destination, 307);
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch {
    return NextResponse.json(
      { detail: "工作空间入口正在配置，请稍后重试。" },
      { status: 503 },
    );
  }
}

export const config = { matcher: "/:path*" };
