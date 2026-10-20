import { forwardLocalEditor } from "@/lib/server/editorProxy";
export async function POST(request: Request) {
  return forwardLocalEditor(request, "/api/knowledge/preview", 850_000);
}
