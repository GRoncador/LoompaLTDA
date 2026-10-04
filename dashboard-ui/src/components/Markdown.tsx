/** The .md files the factory writes (spec, plan, tasks, research), rendered for a person to read
 * instead of the raw text the models produced (plan 11.4). Small on purpose: these files follow
 * Spec Kit's shape — headings, lists, checklists, tables and fenced code — so this covers that
 * and nothing more. React elements are built by hand, so nothing from a model is ever HTML. */
import type { ReactNode } from "react";

export default function Markdown({ text }: { text: string }) {
  if (!text.trim()) return <p className="text-sm text-slate-400">(ainda não gerado)</p>;
  return <div className="max-w-3xl space-y-2 text-sm leading-relaxed text-slate-300">{blocks(text)}</div>;
}

const H = ["text-xl font-semibold text-slate-100", "text-lg font-semibold text-slate-100", "text-base font-semibold text-slate-200", "text-sm font-semibold text-slate-200"];

function blocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  const key = () => `b${out.length}`;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    // fenced code, kept monospaced and scrollable
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      out.push(
        <pre key={key()} className="scroll-thin overflow-x-auto rounded-md border border-line bg-ink/70 p-2 font-mono text-[11px] text-slate-300">
          {lang && <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">{lang}</div>}
          {body.join("\n")}
        </pre>,
      );
      continue;
    }

    const head = /^(#{1,6})\s+(.*)$/.exec(line);
    if (head) {
      const level = Math.min(head[1].length, 4);
      out.push(<div key={key()} className={`${H[level - 1]} ${level <= 2 ? "mt-4 border-b border-line pb-1" : "mt-3"}`}>{inline(head[2])}</div>);
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push(<hr key={key()} className="border-line" />); i++; continue; }

    if (line.startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) body.push(lines[i++].replace(/^>\s?/, ""));
      out.push(<blockquote key={key()} className="border-l-2 border-slate-600 pl-3 text-slate-400">{inline(body.join(" "))}</blockquote>);
      continue;
    }

    // pipe table: header, separator, rows
    if (line.includes("|") && /^\s*\|?[-: |]+\|[-: |]+$/.test(lines[i + 1] ?? "")) {
      const cells = (l: string) => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      const header = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|")) rows.push(cells(lines[i++]));
      out.push(
        <div key={key()} className="scroll-thin overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead><tr>{header.map((c, n) => <th key={n} className="border-b border-line px-2 py-1 text-left font-semibold text-slate-200">{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((r, n) => <tr key={n}>{r.map((c, m) => <td key={m} className="border-b border-line/60 px-2 py-1 align-top">{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }

    // lists, including the checklists of tasks.md
    const bullet = /^\s*([-*+]|\d+[.)])\s+/;
    if (bullet.test(line)) {
      const ordered = /^\s*\d+[.)]\s/.test(line);
      const items: ReactNode[] = [];
      while (i < lines.length && bullet.test(lines[i])) {
        const body = lines[i].replace(bullet, "");
        const box = /^\[([ xX])\]\s*/.exec(body);
        items.push(
          box ? (
            <li key={items.length} className={`flex gap-2 ${box[1] === " " ? "" : "text-slate-500"}`}>
              <span className={box[1] === " " ? "text-slate-500" : "text-emerald-400"}>{box[1] === " " ? "☐" : "☑"}</span>
              <span className={box[1] === " " ? "" : "line-through decoration-slate-600"}>{inline(body.slice(box[0].length))}</span>
            </li>
          ) : (
            <li key={items.length} className="ml-4 list-item list-disc">{inline(body)}</li>
          ),
        );
        i++;
      }
      out.push(ordered
        ? <ol key={key()} className="ml-4 list-decimal space-y-1">{items}</ol>
        : <ul key={key()} className="space-y-1">{items}</ul>);
      continue;
    }

    const body: string[] = [];
    while (i < lines.length && lines[i].trim() && !bullet.test(lines[i]) && !lines[i].startsWith("#") && !lines[i].startsWith("```") && !lines[i].startsWith(">")) body.push(lines[i++]);
    out.push(<p key={key()}>{inline(body.join(" "))}</p>);
  }
  return out;
}

/** Inline marks, innermost last so a `code` span is never re-parsed: code, bold, italic, link. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*|_[^_]+_)|(\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) out.push(<code key={out.length} className="rounded bg-ink/80 px-1 py-0.5 font-mono text-[11px] text-sky-200">{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("**")) out.push(<b key={out.length} className="font-semibold text-slate-100">{tok.slice(2, -2)}</b>);
    else if (tok.startsWith("[")) {
      const [, label, href] = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok)!;
      out.push(<a key={out.length} href={href} target="_blank" rel="noreferrer" className="text-brand hover:underline">{label}</a>);
    } else out.push(<i key={out.length} className="text-slate-200">{tok.slice(1, -1)}</i>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
