// The Loompas' names as the founder reads them (12/10). The backend and every prompt keep the
// English names ("Master Loompa", role `master`): models read English, and usage rows, configs
// and A/B arms are keyed by them. Only what the panel shows goes through here.

export const ROLE_NAME: Record<string, string> = {
  master: "Diretor",
  product: "Analista de Negócio", // the Analyst writing a spec (ADR-0045)
  analyst: "Analista de Negócio",
  product_owner: "Product Owner",
  product_manager: "Gerente de Produto",
  architect: "Arquiteto",
  worker: "Programador",
  inspector: "Revisor",
  deployer: "Deployer",
  designer: "Designer",
  ux: "UX",
  storyteller: "Redator",
  metrics: "Analista de Dados",
  compliance: "Compliance",
  kaizen: "Kaizen",
  ops: "Operações",
  finance: "Financeiro",
  support: "Suporte",
  founder: "Você",
  factory: "Fábrica",
};

// the English display name each agent records itself under, minus " Loompa"
const BY_DISPLAY: Record<string, string> = {
  Master: "master",
  Spec: "product",
  Product: "product",
  Analyst: "analyst",
  "Product Owner": "product_owner",
  "Product Manager": "product_manager",
  Architect: "architect",
  Worker: "worker",
  Inspector: "inspector",
  Deployer: "deployer",
  Designer: "designer",
  UX: "ux",
  Storyteller: "storyteller",
  Metrics: "metrics",
  Compliance: "compliance",
  Kaizen: "kaizen",
  Ops: "ops",
  Finance: "finance",
  Support: "support",
};

/** "Worker Loompa (OpenCode)" → "Programador (OpenCode)"; a name it does not know comes back
 * without the " Loompa", as the panel always showed it. */
export function loompaName(name: string | null | undefined): string {
  if (!name) return "";
  const m = name.match(/^(.*?) Loompa(.*)$/);
  const base = m ? m[1] : name;
  const role = BY_DISPLAY[base];
  return (role ? ROLE_NAME[role] : base) + (m ? m[2] : "");
}

