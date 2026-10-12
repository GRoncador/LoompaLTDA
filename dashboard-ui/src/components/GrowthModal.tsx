import { useEffect, useState } from "react";
import { api } from "../api";
import type { GrowthView } from "../types";
import { designUrl } from "./DesignScreens";
import { Modal } from "./Modal";
import { Icon } from "./Icon";

const KIND_LABEL: Record<string, string> = { plan: "Plano de marketing", copy: "Textos", emails: "Sequência de e-mails", posts: "Posts e anúncios" };
const STATUS: Record<string, string> = { draft: "rascunho na Caixa de Entrada", approved: "aprovado", discarded: "descartado", adjusting: "refazendo com o seu ajuste" };

/** Growth, first rung (plan 17): drafts the founder reviews. Nothing is sent or published by the
 *  factory; approved pieces are files to use by hand. */
export default function GrowthModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const [view, setView] = useState<GrowthView | null>(null);
  const [kind, setKind] = useState("plan");
  const [brief, setBrief] = useState("");
  const [nets, setNets] = useState<string[]>(["instagram", "linkedin"]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const load = () => api.growth(slug).then(setView).catch((e) => setNote(String(e)));
  useEffect(() => { load(); }, [slug]);
  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setNote(null);
    try { setNote(await fn()); await load(); } catch (e) { setNote(String(e)); } finally { setBusy(false); }
  };
  return (
    <Modal title={<><Icon name="megaphone" className="mr-1.5 inline h-4 w-4 align-[-2px]" />Growth · peças para você revisar</>} onClose={onClose} className="w-[96vw] max-w-5xl">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-slate-400">A fábrica escreve e desenha; você revisa e publica. Nada é enviado, publicado ou pago por aqui.</p>
        <div className="space-y-2 rounded-md border border-line bg-panel/40 p-2">
          <div className="flex flex-wrap gap-1">
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <button key={k} className={kind === k ? "btn-primary" : "btn-ghost"} onClick={() => setKind(k)}>{label}</button>
            ))}
          </div>
          <textarea className="w-full rounded-md border border-line bg-ink px-2 py-1" rows={2} value={brief} onChange={(e) => setBrief(e.target.value)}
            placeholder={kind === "posts" ? "Sobre o que é (ex.: lançamento, uma função nova)" : "O que você quer (opcional)"} />
          {kind === "posts" && view && (
            <div className="flex flex-wrap gap-2 text-xs">
              {view.networks.map((n) => (
                <label key={n.key} className="flex items-center gap-1">
                  <input type="checkbox" checked={nets.includes(n.key)} onChange={(e) => setNets(e.target.checked ? [...nets, n.key] : nets.filter((x) => x !== n.key))} />
                  {n.label} <span className="text-slate-500">{n.size}</span>
                </label>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <button className="btn-primary" disabled={busy || (kind === "posts" && nets.length === 0)} onClick={() => run(async () => {
              const r = await api.growthPiece(slug, { kind, brief, networks: nets });
              return `${r.id}: rascunho na Caixa de Entrada.`;
            })}>{busy ? "escrevendo…" : "Criar rascunho"}</button>
            <button className="btn-ghost ml-auto" disabled={busy} title="A landing é uma história de código: passa pelo Product Owner e pelas telas antes do código" onClick={() => run(async () => {
              const r = await api.growthLanding(slug);
              return r.story_id ? `Landing page no backlog como ${r.story_id}.` : "O Product Owner não aceitou: veja a conversa de revisão.";
            })}><Icon name="globe" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />Pedir a landing page</button>
          </div>
          {note && <p className="text-xs text-slate-300">{note}</p>}
        </div>
        {view?.pieces.length === 0 && <p className="text-slate-400">Nenhuma peça ainda.</p>}
        <div className="space-y-2">
          {[...(view?.pieces ?? [])].reverse().map((p) => (
            <div key={p.id} className="rounded-md border border-line p-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-slate-500">{p.id}</span>
                <span className="font-medium text-slate-100">{p.title || KIND_LABEL[p.kind]}</span>
                <span className="chip bg-slate-800 text-slate-300">{STATUS[p.status] ?? p.status} · rodada {p.round}</span>
                <span className="ml-auto flex gap-2">
                  {p.files.map((f) => <a key={f} className="text-brand hover:underline" href={designUrl(slug, `growth/${p.id}/${f}`)} target="_blank" rel="noreferrer">{f}</a>)}
                </span>
              </div>
              {p.images.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-2">
                  {p.images.map((img) => (
                    <a key={img} href={designUrl(slug, `growth/${p.id}/${img}`)} target="_blank" rel="noreferrer">
                      <img src={designUrl(slug, `growth/${p.id}/${img}`)} alt={img} className="max-h-32 rounded border border-line" />
                    </a>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
