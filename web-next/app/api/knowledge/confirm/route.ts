import { forwardLocalEditor } from "@/lib/server/editorProxy";
export async function POST(request: Request) {
  return forwardLocalEditor(request, "/api/knowledge/confirm", 16_384);
}
