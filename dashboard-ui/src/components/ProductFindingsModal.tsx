import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProductFinding } from "../types";
import { Modal } from "./Modal";

const KIND: Record<string, string> = { bug: "defeito", tech_debt: "débito técnico", opportunity: "oportunidade", risk: "risco", resolution: "solução registrada" };

/** What the Kaizen loop caught in the product today, once each (8.4). The factory's own
 *  problems are elsewhere: the 🏭 Fábrica tab. */
export default function ProductFindingsModal({ slug, onClose, onOpenStory }: { slug: string; onClose: () => void; onOpenStory: (id: string) => void }) {
  const [rows, setRows] = useState<ProductFinding[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.productFindings(slug).then(setRows).catch((e) => setErr(String(e))); }, [slug]);
  return (
    <Modal title="💡 Achados no produto hoje" onClose={onClose}>
      <p className="text-xs text-slate-400">O que a fábrica notou no código do produto enquanto construía, contado uma vez cada. Os que viraram card estão no backlog, priorizados pelo Product Owner.</p>
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      <ul className="mt-3 space-y-1.5">
        {rows === null && <li className="text-xs text-slate-500">carregando…</li>}
        {rows?.length === 0 && <li className="text-xs text-slate-500">Nada novo hoje.</li>}
        {rows?.map((r, i) => (
          <li key={i} className="rounded-md border border-line bg-panel p-2 text-xs">
            <div className="flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
              <span className="chip bg-lime-900/50 text-lime-200">{KIND[r.kind] ?? r.kind}</span>
              {r.story_id && <span>notado em <button className="text-sky-300 hover:underline" onClick={() => onOpenStory(r.story_id as string)}>{r.story_id}</button></span>}
              {r.card && <span>· card <button className="text-sky-300 hover:underline" onClick={() => onOpenStory(r.card as string)}>{r.card}</button></span>}
            </div>
            <div className="mt-0.5 font-medium text-slate-100">{r.title}</div>
            {r.detail && <div className="line-clamp-2 text-slate-400">{r.detail}</div>}
          </li>
        ))}
      </ul>
    </Modal>
  );
}
