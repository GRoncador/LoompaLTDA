import type { DesignScreen } from "../types";

export const designUrl = (slug: string, path: string) => `/api/factories/${slug}/design/file/${path}`;

const STATE_LABEL: Record<string, string> = { main: "principal", empty: "vazio", error: "erro", loading: "carregando" };

/** Screens drawn by the UX Loompa (ADR-0039): each state at computer and phone width, side by side.
 *  A click opens the picture; "abrir a tela" opens the static page. With `notes`, each screen gets a
 *  field for what to adjust in it, sent with "Pedir ajustes". */
export function DesignScreens({ slug, screens, notes, onNote }: {
  slug: string; screens: DesignScreen[]; notes?: Record<string, string>; onNote?: (key: string, value: string) => void;
}) {
  return (
    <div className="mt-2 space-y-3">
      {screens.map((s, i) => {
        const key = `${s.alt ? `Alternativa ${s.alt} · ` : ""}${s.title}${s.state ? ` (${STATE_LABEL[s.state] ?? s.state})` : ""}`;
        return (
          <div key={`${s.html ?? s.title}-${i}`} className="rounded-md border border-line/70 bg-panel/40 p-2">
            <div className="mb-1 flex items-center gap-2 text-xs">
              <span className="font-medium text-slate-200">{key}</span>
              {s.route && <code className="text-[10px] text-slate-500">{s.route}</code>}
              {s.html && <a className="ml-auto text-brand hover:underline" href={designUrl(slug, s.html)} target="_blank" rel="noreferrer">abrir a tela ↗</a>}
            </div>
            <div className="flex items-start gap-2">
              {s.desktop && (
                <a href={designUrl(slug, s.desktop)} target="_blank" rel="noreferrer" className="block" title="Computador (1280 px)">
                  <img src={designUrl(slug, s.desktop)} alt={`${s.title}, no computador`} loading="lazy" className="max-h-48 w-64 rounded border border-line object-cover object-top hover:border-slate-400" />
                </a>
              )}
              {s.mobile && (
                <a href={designUrl(slug, s.mobile)} target="_blank" rel="noreferrer" className="block" title="Celular (390 px)">
                  <img src={designUrl(slug, s.mobile)} alt={`${s.title}, no celular`} loading="lazy" className="max-h-48 w-24 rounded border border-line object-cover object-top hover:border-slate-400" />
                </a>
              )}
              {!s.desktop && !s.mobile && <span className="text-xs text-slate-500">sem foto (navegador indisponível): abra a tela</span>}
            </div>
            {notes && onNote && (
              <input value={notes[key] ?? ""} onChange={(e) => onNote(key, e.target.value)} placeholder="O que ajustar nesta tela (opcional)" className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-0.5 text-xs" />
            )}
          </div>
        );
      })}
    </div>
  );
}
