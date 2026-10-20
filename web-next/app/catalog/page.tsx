import { redirect } from "next/navigation";

export default function CatalogPage() {
  redirect("/?view=catalog");
}
