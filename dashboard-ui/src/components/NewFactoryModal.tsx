import { useEffect, useState } from "react";
import { api } from "../api";
import type { Settings, SettingsPatch } from "../types";
import { Modal } from "./Modal";
import { ProvidersPanel } from "./SettingsModal";

export default function NewFactoryModal({ onClose }: { onClose: (createdSlug?: string) => void }) {
  const [folder, setFolder] = useState("");
  const [projectsDir, setProjectsDir] = useState<string | null>(null);
  const [dirDraft, setDirDraft] = useState("");
  const [editingDir, setEditingDir] = useState(false);
  const [name, setName] = useState("");
  const [stack, setStack] = useState("custom");
  const [mission, setMission] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.hub().then((h) => {
      setProjectsDir(h.projects_dir);
      setDirDraft(h.projects_dir ?? h.suggested_projects_dir);
      setEditingDir(!h.projects_dir);
    }).catch((e) => setErr(String(e)));
  }, []);
  const saveDir = async () => {
    setErr(null);
    try { const r = await api.setProjectsDir(dirDraft); setProjectsDir(r.projects_dir); setEditingDir(false); }
    catch (e) { setErr(String(e)); }
  };
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.addFactory({ folder, name: name || undefined, stack, mission });
      setReport(r.report); setSlug(r.slug); setStep(2);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  useEffect(() => { if (step === 3 && slug) api.settings(slug).then(setSettings).catch((e) => setErr(String(e))); }, [step, slug]);
  const save = async (patch: SettingsPatch) => {
    if (!slug) return;
    try { const r = await api.updateSettings(slug, patch); setSettings(r.settings); setNotes(r.changes); } catch (e) { setErr(String(e)); }
  };
  return (
    <Modal title={`+ Nova Fábrica · passo ${step}/3`} onClose={() => onClose(slug ?? undefined)}>
      {step === 1 && (
        <div className="space-y-3 text-sm">
          {editingDir ? (
            <div className="space-y-1">
              <label className="block">Pasta onde ficam todas as suas fábricas
                <input value={dirDraft} onChange={(e) => setDirDraft(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-1" />
              </label>
              <p className="text-xs text-slate-400">Escolhida uma vez: cada nova fábrica é só uma pasta aqui dentro.</p>
              <div className="text-right"><button className="btn-ghost" disabled={!dirDraft.trim()} onClick={saveDir}>Usar esta pasta</button></div>
            </div>
          ) : (
            <label className="block">Pasta da fábrica (nova ou já existente)
              <div className="mt-1 flex items-center gap-1">
                <span className="truncate text-xs text-slate-400" title={projectsDir ?? ""}>{projectsDir}/</span>
                <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="meu-saas" className="min-w-0 flex-1 rounded-md border border-line bg-ink px-2 py-1" />
              </div>
              <button type="button" className="mt-1 text-xs text-slate-400 underline" onClick={() => setEditingDir(true)}>trocar a pasta das fábricas</button>
            </label>
          )}
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
          <p className="rounded-md border border-line/60 bg-slate-900/50 p-2 text-xs text-slate-400">
            A fábrica nasce usando a OpenRouter: uma chave alcança todos os modelos. No próximo passo você cola
            a chave, e em <strong className="text-slate-300">Configurações › Modelos</strong> escolhe quais modelos cada
            Loompa usa (ou pede uma sugestão inteligente).
          </p>
          {err && <p className="text-xs text-red-300">{err}</p>}
          <div className="text-right"><button className="btn-primary" disabled={busy || editingDir || !folder.trim()} onClick={submit}>{busy ? "Escaneando…" : "Conectar fábrica"}</button></div>
        </div>
      )}
      {step === 2 && (
        <>
          <pre className="scroll-thin max-h-[50vh] overflow-y-auto whitespace-pre-wrap text-xs text-slate-300">{report}</pre>
          <div className="mt-3 flex justify-end gap-2">
            <button className="btn-ghost" onClick={() => onClose(slug ?? undefined)}>Pular chaves</button>
            <button className="btn-primary" onClick={() => setStep(3)}>Provedores e chaves →</button>
          </div>
        </>
      )}
      {step === 3 && slug && (
        <div className="scroll-thin max-h-[70vh] space-y-3 overflow-y-auto text-sm">
          <p className="text-slate-400">Cole a chave dos provedores que quiser usar e teste a conexão. Sem chave, a fábrica roda em simulação.</p>
          {err && <p className="text-xs text-red-300">{err}</p>}
          {notes.length > 0 && <p className="text-xs text-emerald-300">✔ {notes.join(" · ")}</p>}
          {settings ? <ProvidersPanel slug={slug} s={settings} onSave={save} /> : <p className="text-slate-400">carregando…</p>}
          <div className="text-right"><button className="btn-primary" onClick={() => onClose(slug)}>Abrir fábrica</button></div>
        </div>
      )}
    </Modal>
  );
}
