import { useRef, useState } from "react";
import { api } from "../api";

export default function MeetingModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const [goals, setGoals] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ stories: { id: string; title: string }[]; clarifications: string[] } | null>(null);
  const [recording, setRecording] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  const toggleRecord = async () => {
    if (recording) { rec.current?.stop(); setRecording(false); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunks.current = [];
      mr.ondataavailable = (e) => chunks.current.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setBusy(true);
        try {
          const { text } = await api.transcribe(slug, new Blob(chunks.current, { type: "audio/webm" }));
          setGoals((g) => (g ? `${g}\n${text}` : text));
        } catch (e) { setErr("Ditado por voz indisponível: instale loompa-core[voice]."); } finally { setBusy(false); }
      };
      mr.start();
      rec.current = mr;
      setRecording(true);
    } catch { setErr("Microfone não autorizado."); }
  };

  const submit = async (run: boolean) => {
    setBusy(true); setErr(null);
    try { setResult(await api.meeting(slug, goals, run)); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <Modal title="☀️ Reunião matinal com o Master Loompa" onClose={onClose}>
      {!result ? (
        <>
          <p className="text-sm text-slate-400">Diga as metas de hoje em linguagem livre. O Master Loompa decompõe em histórias, especifica e despacha para a esteira.</p>
          <textarea value={goals} onChange={(e) => setGoals(e.target.value)} rows={7} placeholder={"Ex.: Hoje quero a recuperação de senha pronta; refatorar o webhook de cobrança; e um relatório mensal em CSV."} className="mt-3 w-full rounded-md border border-line bg-ink p-3 text-sm" />
          {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
          <div className="mt-3 flex items-center gap-2">
            <button className={recording ? "btn bg-red-600 text-white" : "btn-ghost"} onClick={toggleRecord} disabled={busy}>{recording ? "⏹ Parar ditado" : "🎙 Ditar (Whisper local)"}</button>
            <div className="mx-auto" />
            <button className="btn-ghost" disabled={busy || !goals.trim()} onClick={() => submit(false)}>Só planejar</button>
            <button className="btn-primary" disabled={busy || !goals.trim()} onClick={() => submit(true)}>{busy ? "Planejando…" : "Planejar e ligar a esteira ▶"}</button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-emerald-300">✔ {result.stories.length} histórias no Kanban.</p>
          <ul className="mt-2 space-y-1 text-sm">{result.stories.map((s) => <li key={s.id}><span className="text-slate-500">{s.id}</span> {s.title}</li>)}</ul>
          {result.clarifications.length > 0 && (
            <div className="mt-3 rounded-md border border-amber-700/60 bg-amber-900/30 p-2 text-xs text-amber-100">
              Perguntas enviadas à Caixa de Entrada: {result.clarifications.map((c, i) => <div key={i}>• {c}</div>)}
            </div>
          )}
          <div className="mt-4 text-right"><button className="btn-primary" onClick={onClose}>Fechar</button></div>
        </>
      )}
    </Modal>
  );
}

export function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="card w-full max-w-2xl p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold">{title}</h3>
          <button className="text-slate-400 hover:text-white" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
