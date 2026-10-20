import { forwardLocalEditor } from "@/lib/server/editorProxy";
export async function POST(request: Request) {
  return forwardLocalEditor(request, "/api/settings", 16_384);
}
