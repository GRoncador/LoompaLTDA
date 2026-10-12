import { useEffect, useState } from "react";
import { api } from "../api";
import type { DesignOverview, DesignReference, Message, StoryDesign } from "../types";
import { DesignScreens, designUrl } from "./DesignScreens";
import { MessageCard } from "./Inbox";
import { Modal } from "./Modal";
import { Icon } from "./Icon";

type Tab = "approve" | "base" | "references" | "history";

const KIND_LABEL: Record<DesignReference["kind"], string> = { image: "imagem", link: "site", figma: "Figma", text: "texto" };

/** The design gate in one place (ADR-0039): the screens waiting for approval, all together at the
 *  start of a sprint; the visual base; the founder's references; and each story's rounds. */
export default function DesignModal({ slug, story, onClose, onReply, onOpenStory }: {
  slug: string; story?: string | null; onClose: () => void;
  onReply: (m: Message, option: string | null, text: string | null, decisions?: Record<string, string>, answers?: Record<string, string>) => Promise<void>;
  onOpenStory: (id: string) => void;
}) {
  const [data, setData] = useState<DesignOverview | null>(null);
  const [tab, setTab] = useState<Tab>(story ? "history" : "approve");
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.design(slug).then(setData).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, [slug]);
  const reply = async (...args: Parameters<typeof onReply>) => { await onReply(...args); await load(); };
  const tabs: [Tab, string][] = [
    ["approve", `Para aprovar${data?.pending.length ? ` (${data.pending.length})` : ""}`],
    ["base", "Base visual"],
    ["references", "Referências"],
    ["history", "Telas por história"],
  ];
  return (
    <Modal title={<><Icon name="palette" className="mr-1.5 inline h-4 w-4 align-[-2px]" />Design</>} onClose={onClose} className="w-[96vw] max-w-6xl">
      <div className="mb-3 flex flex-wrap gap-2 border-b border-line pb-2">
        {tabs.map(([t, label]) => <button key={t} className={tab === t ? "btn-primary" : "btn-ghost"} onClick={() => setTab(t)}>{label}</button>)}
      </div>
      {err && <p className="text-xs text-red-300">{err}</p>}
      {!data ? <p className="text-sm text-slate-400">carregando…</p> : (
        <div className="space-y-3 text-sm">
          {tab === "approve" && (
            data.pending.length === 0
              ? <p className="text-slate-400">Nenhuma tela esperando você. As telas das histórias do sprint chegam aqui juntas, antes do código.</p>
              : data.pending.map((p) => (
                <MessageCard key={p.message.id} m={p.message} onReply={reply} onArchive={async () => {}} onOpenStory={onOpenStory} onOpenSprint={() => {}} />
              ))
          )}
          {tab === "base" && <BaseView slug={slug} data={data} />}
          {tab === "references" && <References slug={slug} refs={data.references} onChange={load} />}
          {tab === "history" && <History slug={slug} stories={data.stories} initial={story ?? null} />}
          {!data.sees && (
            <p className="text-[11px] text-slate-500">
              Nenhum modelo com visão configurado (Configurações › Modelos): o UX não olha as próprias telas nem compara o produto com as aprovadas; a conferência em código continua valendo.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}

function BaseView({ slug, data }: { slug: string; data: DesignOverview }) {
  if (!data.base) return <p className="text-slate-400">A base visual nasce na primeira história com tela: o UX propõe duas e você escolhe uma.</p>;
  const colours = (data.base.tokens.color ?? {}) as Record<string, string>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {Object.entries(colours).map(([name, value]) => (
          <div key={name} className="flex items-center gap-1 rounded border border-line px-2 py-1 text-xs">
            <span className="inline-block h-4 w-4 rounded border border-line" style={{ background: value }} />
            <span className="text-slate-300">{name}</span><code className="text-slate-500">{value}</code>
          </div>
        ))}
      </div>
      <DesignScreens slug={slug} screens={[{ title: "Prancha de estilo", state: "", html: data.base.board || undefined, desktop: data.base.desktop, mobile: data.base.mobile }]} />
    </div>
  );
}

function References({ slug, refs, onChange, story }: { slug: string; refs: DesignReference[]; onChange: () => void; story?: string }) {
  const [kind, setKind] = useState<DesignReference["kind"]>("image");
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const add = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.addReference(slug, { kind, title, note, url, file, story_id: story });
      setMsg(r.note || "Referência guardada."); setTitle(""); setNote(""); setUrl(""); setFile(null); onChange();
    } catch (e) { setMsg(String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-3">
      <div className="space-y-2 rounded-md border border-line bg-panel/40 p-2 text-xs">
        <div className="flex flex-wrap gap-1">
          {(["image", "link", "figma", "text"] as const).map((k) => (
            <button key={k} type="button" className={kind === k ? "btn-primary" : "btn-ghost"} onClick={() => setKind(k)}>{KIND_LABEL[k]}</button>
          ))}
        </div>
        {kind === "image" && <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
        {(kind === "link" || kind === "figma") && <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={kind === "figma" ? "link de um quadro do Figma (com node-id)" : "https://…"} className="w-full rounded-md border border-line bg-ink px-2 py-1" />}
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="nome (opcional)" className="w-full rounded-md border border-line bg-ink px-2 py-1" />
        <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder={kind === "text" ? "ex.: minimalista, como o app X só que mais claro" : "o que você gosta nela (opcional)"} className="w-full rounded-md border border-line bg-ink px-2 py-1" rows={2} />
        <div className="flex items-center justify-between">
          <span className="text-slate-500">{story ? `Só para a ${story}.` : "Vale para o produto todo."} O UX lê como material, nunca como ordem.</span>
          <button className="btn-primary" disabled={busy || (kind === "image" ? !file : kind === "text" ? !note.trim() : !url.trim())} onClick={add}>{busy ? "guardando…" : "Adicionar"}</button>
        </div>
        {msg && <p className="text-slate-300">{msg}</p>}
      </div>
      {refs.length === 0 && <p className="text-slate-400">Nenhuma referência ainda.</p>}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {refs.map((r) => (
          <div key={r.id} className="rounded-md border border-line p-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="chip bg-slate-800 text-slate-300">{KIND_LABEL[r.kind]}</span>
              <span className="font-medium text-slate-200">{r.title || r.id}</span>
              {r.story_id && <span className="text-slate-500">{r.story_id}</span>}
              <button className="ml-auto text-slate-500 hover:text-red-300" onClick={async () => { await api.removeReference(slug, r.id); onChange(); }}>remover</button>
            </div>
            {r.file && <img src={designUrl(slug, `references/${r.file}`)} alt={r.title || r.id} className="mt-1 max-h-40 w-full rounded object-cover object-top" />}
            {r.url && <a href={r.url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-brand hover:underline">{r.url}</a>}
            {r.note && <p className="mt-1 text-slate-400">{r.note}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}

function History({ slug, stories, initial }: { slug: string; stories: string[]; initial: string | null }) {
  const [id, setId] = useState<string | null>(initial ?? stories[stories.length - 1] ?? null);
  const [data, setData] = useState<StoryDesign | null>(null);
  const load = () => { if (id) api.storyDesign(slug, id).then(setData).catch(() => setData(null)); };
  useEffect(load, [slug, id]);
  const all = [...new Set([...(initial ? [initial] : []), ...stories])];
  return (
    <div className="space-y-3">
      <select className="rounded-md border border-line bg-ink px-2 py-1" value={id ?? ""} onChange={(e) => setId(e.target.value)}>
        {all.length === 0 && <option value="">(nenhuma história com telas)</option>}
        {all.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      {data && (
        <>
          {data.approved && (
            <section>
              <h4 className="font-semibold text-emerald-300"><Icon name="check" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />Aprovada (rodada {data.approved.round}{data.approved.alternative ? `, alternativa ${data.approved.alternative}` : ""})</h4>
              <DesignScreens slug={slug} screens={data.approved.screens} />
            </section>
          )}
          {data.rounds.map((r) => (
            <details key={r.round} open={!data.approved && r === data.rounds[0]} className="rounded-md border border-line/60 p-2">
              <summary className="cursor-pointer text-slate-200">Rodada {r.round} · {r.screens.length} tela(s)</summary>
              {r.assumptions.length > 0 && <p className="mt-1 text-xs text-slate-400">Decidido pelo UX: {r.assumptions.join("; ")}</p>}
              {r.problems.length > 0 && <p className="mt-1 text-xs text-amber-300">Conferência: {r.problems.join("; ")}</p>}
              <DesignScreens slug={slug} screens={r.screens} />
            </details>
          ))}
          <References slug={slug} refs={data.references.filter((r) => r.story_id === id)} story={id ?? undefined} onChange={load} />
        </>
      )}
    </div>
  );
}
