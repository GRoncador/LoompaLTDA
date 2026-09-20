import React from "react";

interface ProviderIconProps {
  provider: string;
  model?: string;
  className?: string;
}

export function normalizeVendor(provider: string, model?: string): string {
  const p = (provider || "").toLowerCase();
  const m = (model || "").toLowerCase().replace(/^~/, "");

  if (p === "openrouter" && m.includes("/")) {
    return m.split("/")[0];
  }
  if (m.startsWith("gpt") || m.startsWith("o1") || m.startsWith("o3") || m.startsWith("chatgpt")) return "openai";
  if (m.startsWith("claude")) return "anthropic";
  if (m.startsWith("gemini")) return "google";
  if (m.startsWith("deepseek")) return "deepseek";
  if (m.startsWith("llama")) return "meta";
  if (m.startsWith("qwen")) return "qwen";
  if (m.startsWith("grok")) return "x-ai";
  if (m.startsWith("mistral") || m.startsWith("codestral")) return "mistralai";
  if (m.startsWith("glm")) return "z-ai";

  return p;
}

export function ProviderIcon({ provider, model, className = "w-4 h-4 inline-block" }: ProviderIconProps) {
  const vendor = normalizeVendor(provider, model);

  const wrap = (svg: React.ReactNode, title: string) => (
    <span className="inline-flex items-center justify-center" title={title}>
      {svg}
    </span>
  );

  switch (vendor) {
    case "openai":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 8.784a4.472 4.472 0 0 1 2.36-1.99v5.679a.763.763 0 0 0 .388.676l5.82 3.359-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 8.784zm16.597 3.855l-5.833-3.387L15.124 8.1a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.674 8.169v-5.698a.79.79 0 0 0-.414-.723zm2.01-4.27a4.455 4.455 0 0 1-.538 3.014l-.142-.085-4.779-2.759a.795.795 0 0 0-.792 0L8.853 11.91V9.577a.08.08 0 0 1 .033-.062l4.84-2.796a4.5 4.5 0 0 1 6.174 1.646zM7.4 12.87L9.42 11.7a.071.071 0 0 1 .038-.052V6.065a4.504 4.504 0 0 1 7.37-3.454l-.141.08-4.779 2.759a.79.79 0 0 0-.392.681v6.74z"/>
        </svg>,
        "OpenAI"
      );
    case "anthropic":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M17.47 3.6h-3.41L7.54 20.4h3.33l1.45-3.88h5.36l1.45 3.88h3.34L17.47 3.6zm-4.04 10.3l1.83-4.91 1.83 4.91h-3.66zM4.78 3.6L1.44 20.4h3.32l.86-4.32h2.52l.48-2.42H6.1l1.52-7.66H4.78z"/>
        </svg>,
        "Anthropic"
      );
    case "google":
    case "gemini":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 24C12 17.3726 6.62742 12 0 12C6.62742 12 12 6.62742 12 0C12 6.62742 17.3726 12 24 12C17.3726 12 12 17.3726 12 24Z"/>
        </svg>,
        "Google Gemini"
      );
    case "deepseek":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 3C6.477 3 2 7.03 2 12c0 2.5 1.15 4.76 3 6.35V21l3.08-1.54C9.37 19.8 10.66 20 12 20c5.523 0 10-4.03 10-9s-4.477-9-10-9zm-1 12H9v-2h2v2zm4 0h-2v-2h2v2zm0-4H9V9h6v2z"/>
        </svg>,
        "DeepSeek"
      );
    case "meta":
    case "meta-llama":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 7.5C9.5 4 6 4 3.5 6.5C1 9 1 13 3.5 15.5C6 18 9.5 18 12 14.5C14.5 18 18 18 20.5 15.5C23 13 23 9 20.5 6.5C18 4 14.5 4 12 7.5ZM6.5 14C5 14 3.8 12.8 3.8 11C3.8 9.2 5 8 6.5 8C8.5 8 10.5 10.5 11.5 12C10.5 13.5 8.5 14 6.5 14ZM17.5 14C15.5 14 13.5 11.5 12.5 10C13.5 8.5 15.5 8 17.5 8C19 8 20.2 9.2 20.2 11C20.2 12.8 19 14 17.5 14Z"/>
        </svg>,
        "Meta Llama"
      );
    case "qwen":
    case "alibaba":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L3 7v10l9 5 9-5V7l-9-5zm0 3.3l6 3.33v6.74L12 18.7l-6-3.33V8.63l6-3.33zm-1.5 5.2v3h3v-3h-3z"/>
        </svg>,
        "Qwen"
      );
    case "x-ai":
    case "grok":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
        </svg>,
        "xAI Grok"
      );
    case "mistral":
    case "mistralai":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M3 4h3.5v3.5H3V4zm14.5 0H21v3.5h-3.5V4zM6.5 7.5H10V11H6.5V7.5zm7.5 0h3.5V11H14V7.5zM10 11h4v3.5h-4V11zm-7 5h3.5v3.5H3V16zm14.5 0H21v3.5h-3.5V16z"/>
        </svg>,
        "Mistral AI"
      );
    case "z-ai":
    case "glm":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M4 4h16v4L10 16h10v4H4v-4l10-8H4V4z"/>
        </svg>,
        "Zhipu GLM"
      );
    case "openrouter":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L2 7v10l10 5 10-5V7L12 2zm0 2.8l7 3.5v7.4l-7 3.5-7-3.5V8.3l7-3.5zm-1 4.2v6h2v-6h-2z"/>
        </svg>,
        "OpenRouter"
      );
    case "groq":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" fill="none" strokeDasharray="40 10"/>
        </svg>,
        "Groq"
      );
    default:
      return (
        <span className="inline-flex items-center justify-center rounded bg-slate-800 text-[10px] font-bold text-slate-300 uppercase px-1 py-0.5 border border-slate-700" title={vendor}>
          {vendor.slice(0, 3)}
        </span>
      );
  }
}
