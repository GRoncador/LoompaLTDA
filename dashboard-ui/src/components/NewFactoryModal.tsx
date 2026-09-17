import { useState } from "react";
import { api } from "../api";
import { Modal } from "./MeetingModal";

export default function NewFactoryModal({ onClose }: { onClose: (createdSlug?: string) => void }) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [stack, setStack] = useState("custom");
  const [mission, setMission] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.addFactory({ path, name: name || undefined, stack, mission }); setReport(r.report); setSlug(r.slug); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  return (
    <Modal title="+ Nova Fábrica" onClose={() => onClose(slug ?? undefined)}>
      {!report ? (
        <div className="space-y-3 text-sm">
          <label className="block">Caminho do repositório (novo ou existente)<input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/eu/projetos/meu-saas" className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-1" /></label>
          <label className="block">Nome do produto<input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-1" /></label>
          <label className="block">Missão (uma frase)<input value={mission} onChange={(e) => setMission(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-1" /></label>
          <label className="block">Stack (só para projetos novos)
            <select value={stack} onChange={(e) => setStack(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-1">
              <option value="python-fastapi">Python + FastAPI + PostgreSQL</option>
              <option value="python-cli">Python CLI (Typer)</option>
              <option value="node-react">TypeScript + React (Vite) + Tailwind</option>
              <option value="custom">Custom / brownfield</option>
            </select>
          </label>
          {err && <p className="text-xs text-red-300">{err}</p>}
          <div className="text-right"><button className="btn-primary" disabled={busy || !path} onClick={submit}>{busy ? "Escaneando…" : "Conectar fábrica"}</button></div>
        </div>
      ) : (
        <>
          <pre className="scroll-thin max-h-[50vh] overflow-y-auto whitespace-pre-wrap text-xs text-slate-300">{report}</pre>
          <div className="mt-3 text-right"><button className="btn-primary" onClick={() => onClose(slug ?? undefined)}>Abrir fábrica</button></div>
        </>
      )}
    </Modal>
  );
}
