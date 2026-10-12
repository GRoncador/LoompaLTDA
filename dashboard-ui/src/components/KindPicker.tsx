import type { FactoryKind } from "../types";

export const KIND_OPTIONS: { key: FactoryKind; title: string; note: string }[] = [
  { key: "personal", title: "Pessoal", note: "Para você e quem está por perto: sem pesquisa de mercado nem growth, uma fase só (em uso) e telas aprovadas no modo leve." },
  { key: "commercial", title: "Comercial", note: "Para vender a um mercado: descoberta com concorrentes e hipóteses, fases com critério de saída, peças de marketing e telas aprovadas no modo completo." },
];

/** Personal or commercial (plan 16.1): asked when the factory opens, changeable in the settings. */
export function KindPicker({ value, onChange }: { value: FactoryKind; onChange: (k: FactoryKind) => void }) {
  return (
    <fieldset className="space-y-1">
      <legend className="mb-1">Tipo do produto</legend>
      {KIND_OPTIONS.map((o) => (
        <label key={o.key} className={`flex cursor-pointer gap-2 rounded-md border p-2 text-xs transition-colors ${value === o.key ? "border-brand/60 bg-brand/10" : "border-line/60 bg-raised hover:border-line hover:bg-raised-hover"}`}>
          <input type="radio" name="factory_kind" className="mt-0.5" checked={value === o.key} onChange={() => onChange(o.key)} />
          <span>
            <span className="font-medium text-slate-200">{o.title}</span>
            <span className="mt-0.5 block text-slate-400">{o.note}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
