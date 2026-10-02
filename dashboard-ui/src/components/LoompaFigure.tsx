// The same colours the office gives each role, so a figure on a card or in a history is the
// Loompa you see in the office.
export const ROLE_COLOR: Record<string, string> = {
  master: "#f59e0b", product: "#a78bfa", product_owner: "#a78bfa", architect: "#60a5fa", worker: "#34d399", inspector: "#38bdf8",
  deployer: "#f472b6", storyteller: "#fb7185", metrics: "#94a3b8", compliance: "#c084fc", analyst: "#2dd4bf",
};

export const ROLE_NAME: Record<string, string> = {
  master: "Master", product: "Product", product_owner: "Product Owner", architect: "Architect", worker: "Worker",
  inspector: "Inspector", deployer: "Deployer", analyst: "Analyst", founder: "Você", factory: "Fábrica",
};

/** A tiny Loompa (hair, face, body in the role's colour); `busy` adds the pulsing dot. */
export default function LoompaFigure({ role, busy = false }: { role: string; busy?: boolean }) {
  if (role === "founder") return <span className="inline-flex h-4 w-3 items-center justify-center text-[11px] leading-none">🙂</span>;
  if (role === "factory") return <span className="inline-flex h-4 w-3 items-center justify-center text-[11px] leading-none">⚙️</span>;
  return (
    <span className="relative inline-flex h-4 w-3 shrink-0 flex-col items-center">
      <span className="h-0.5 w-2.5 rounded-sm bg-green-500" />
      <span className="h-1.5 w-2 rounded-sm bg-amber-200" />
      <span className="h-2 w-2.5 rounded-sm" style={{ background: ROLE_COLOR[role] ?? "#e2e8f0" }} />
      {busy && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />}
    </span>
  );
}
