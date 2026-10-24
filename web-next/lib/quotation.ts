import { workspaceRequest } from "./workspace";

export interface PrepareQuotationRequest {
  rule_id: string;
  quantity?: number;
  subject_id?: string;
}

export interface AuthorizedQuotation {
  id: string;
  status: "ready" | "invalidated";
  product_name: string;
  sku: string;
  configuration: string;
  currency: "CNY";
  tax_included: true;
  quantity: number;
  unit_minor: number | null;
  base_minor: number | null;
  fees_minor: number | null;
  discount_minor: number | null;
  total_minor: number | null;
  rule_version: number;
  source_id: string;
  valid_until: string;
  is_demo: boolean;
  message?: string;
  components: { sku: string; name: string; quantity: number; unit_minor: number }[];
  trace: { promotion_id: string; label: string; before_minor: number; deduction_minor: number; after_minor: number }[];
  rejected_promotions: { promotion_id: string; reason: string; missing?: string[] }[];
  optimality: { method: string; evaluated_combinations: number; feasible_combinations: number };
}

export function getQuotation(id: string) {
  return workspaceRequest<AuthorizedQuotation>(`/api/quotations/${encodeURIComponent(id)}`);
}

export function prepareQuotation(request: PrepareQuotationRequest) {
  return workspaceRequest<AuthorizedQuotation | {
    status: "needs_approved_rule" | "needs_current_rule" | "needs_qualification";
    rule_id: string;
    message: string;
  }>("/api/quotations/prepare", request);
}

export function quotationMoney(minor: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(minor / 100);
}
