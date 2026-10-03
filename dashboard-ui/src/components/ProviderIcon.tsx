import React from "react";

interface ProviderIconProps {
  provider: string;
  model?: string;
  className?: string;
}

/** The API providers a factory can hold a key for, in the order the settings screen lists them.
 * `xai` is the company behind Grok; `google` is the vendor tag OpenRouter uses for Gemini. */
export const PROVIDER_NAMES: Record<string, string> = {
  openrouter: "OpenRouter",
  gemini: "Google Gemini",
  anthropic: "Anthropic Claude",
  openai: "OpenAI (ChatGPT)",
  xai: "xAI (Grok)",
  deepseek: "DeepSeek",
  xiaomi: "Xiaomi MiMo",
  zai: "Z.AI (GLM)",
  alibaba: "Alibaba Qwen",
  moonshot: "Moonshot Kimi",
  minimax: "MiniMax",
  ollama: "Ollama (local)",
  tavily: "Tavily",
};

export const VENDOR_NAMES: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google Gemini",
  gemini: "Google Gemini",
  deepseek: "DeepSeek",
  meta: "Meta Llama",
  "meta-llama": "Meta Llama",
  qwen: "Qwen (Alibaba)",
  alibaba: "Qwen (Alibaba)",
  "x-ai": "xAI (Grok)",
  xai: "xAI (Grok)",
  grok: "xAI (Grok)",
  ollama: "Ollama (local)",
  tavily: "Tavily",
  mistral: "Mistral AI",
  mistralai: "Mistral AI",
  "z-ai": "Z.ai (Zhipu)",
  glm: "Z.ai (Zhipu)",
  openrouter: "OpenRouter",
  groq: "Groq",
  amazon: "Amazon Nova",
  microsoft: "Microsoft",
  nvidia: "NVIDIA",
  cohere: "Cohere",
  perplexity: "Perplexity",
  moonshotai: "Moonshot AI",
  minimax: "MiniMax",
  bytedance: "ByteDance",
  baidu: "Baidu",
  tencent: "Tencent",
  xiaomi: "Xiaomi",
};

export function normalizeVendor(provider: string, model?: string): string {
  const p = (provider || "").toLowerCase().replace(/^~/, "");
  const m = (model || "").toLowerCase().replace(/^~/, "");

  if ((p === "openrouter" || !p) && m.includes("/")) {
    return m.split("/")[0].replace(/^~/, "");
  }
  if (p.includes("/")) {
    return p.split("/")[0].replace(/^~/, "");
  }
  if (m.startsWith("gpt") || m.startsWith("o1") || m.startsWith("o3") || m.startsWith("chatgpt")) return "openai";
  if (m.startsWith("claude")) return "anthropic";
  if (m.startsWith("gemini")) return "google";
  if (m.startsWith("deepseek")) return "deepseek";
  if (m.startsWith("llama")) return "meta";
  if (m.startsWith("qwen")) return "qwen";
  if (m.startsWith("grok")) return "x-ai";
  if (p === "xai") return "x-ai";
  if (p === "zai") return "z-ai";
  if (p === "moonshot") return "moonshotai";
  if (p === "gemini") return "google";
  if (m.startsWith("mistral") || m.startsWith("codestral")) return "mistralai";
  if (m.startsWith("glm")) return "z-ai";

  return p;
}

/**
 * Cleans the model display name by stripping brand/provider prefixes.
 * Examples:
 * - "DeepSeek: DeepSeek V4 Flash Latest" -> "DeepSeek V4 Flash Latest"
 * - "Z.ai: GLM Flash Latest (hoje: Z.ai: GLM-4-Flash)" -> "GLM Flash Latest (hoje: GLM-4-Flash)"
 * - "OpenAI: GPT Luna Latest (hoje: OpenAI: GPT-4.1-mini)" -> "GPT Luna Latest (hoje: GPT-4.1-mini)"
 * - "OpenAI: GPT-4o Mini" -> "GPT-4o Mini"
 * - "Meta: Llama 3.3 70B Instruct" -> "Llama 3.3 70B Instruct"
 */
export function cleanModelName(name: string): string {
  if (!name) return "";
  let s = name;
  // 1. Remove provider prefix inside target alias, e.g. "(hoje: Z.ai: GLM-4-Flash)" -> "(hoje: GLM-4-Flash)"
  s = s.replace(/\(hoje:\s*[^:]+:\s*/gi, "(hoje: ");
  // 2. Remove leading provider prefix like "OpenAI: ", "DeepSeek: ", "Z.ai: ", "~Z.ai: "
  s = s.replace(/^([~]?)[A-Za-z0-9._ -]+:\s*/, "$1");
  return s.trim();
}

/**
 * Strips the provider namespace from a model id (e.g. "openai/gpt-4o-mini" -> "gpt-4o-mini", "~z-ai/glm-flash" -> "~glm-flash")
 */
export function cleanModelId(id: string): string {
  if (!id) return "";
  if (id.includes("/")) {
    const isAlias = id.startsWith("~");
    const parts = id.replace(/^~/, "").split("/");
    return isAlias ? `~${parts.slice(1).join("/")}` : parts.slice(1).join("/");
  }
  return id;
}

export function ProviderIcon({ provider, model, className = "w-4 h-4 inline-block" }: ProviderIconProps) {
  const vendor = normalizeVendor(provider, model);
  const providerLabel = VENDOR_NAMES[vendor] || (vendor ? vendor.charAt(0).toUpperCase() + vendor.slice(1) : "Provedor");

  // No `title` here: Safari turns a title on an inline element into a question-mark cursor and
  // never shows the text. Whatever needs a name shows it next to the mark instead.
  const wrap = (svg: React.ReactNode, _name: string) => (
    <span className="inline-flex items-center justify-center" aria-hidden="true">
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
        "Qwen (Alibaba)"
      );
    case "x-ai":
    case "grok":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
        </svg>,
        "xAI (Grok)"
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
        "Z.ai (Zhipu GLM)"
      );
    case "openrouter":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L2 7v10l10 5 10-5V7L12 2zm0 2.8l7 3.5v7.4l-7 3.5-7-3.5V8.3l7-3.5zm-1 4.2v6h2v-6h-2z"/>
        </svg>,
        "OpenRouter"
      );
    case "ollama":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M6.2 3c-1.2 0-2 1.5-2.1 3.3-.05.9.1 1.7.35 2.3C3.55 9.6 3 11 3 12.6c0 2.3 1.1 4.2 2.8 5.4-.3.7-.5 1.5-.5 2.3 0 .4.3.7.7.7h.6c.4 0 .7-.3.7-.7 0-.5.1-1 .3-1.4 1.1.4 2.3.6 3.6.6s2.5-.2 3.6-.6c.2.4.3.9.3 1.4 0 .4.3.7.7.7h.6c.4 0 .7-.3.7-.7 0-.8-.2-1.6-.5-2.3 1.7-1.2 2.8-3.1 2.8-5.4 0-1.6-.55-3-1.45-4 .25-.6.4-1.4.35-2.3C17.8 4.5 17 3 15.8 3c-1.1 0-1.9 1.2-2.15 2.8-.5-.1-1.05-.15-1.65-.15s-1.15.05-1.65.15C10.1 4.2 9.3 3 8.2 3h-2zm3.05 8.1c.7 0 1.25.7 1.25 1.55s-.55 1.55-1.25 1.55S8 13.5 8 12.65s.55-1.55 1.25-1.55zm5.5 0c.7 0 1.25.7 1.25 1.55s-.55 1.55-1.25 1.55-1.25-.7-1.25-1.55.55-1.55 1.25-1.55zM12 15.1c.9 0 1.7.4 1.7.9 0 .5-.8.9-1.7.9s-1.7-.4-1.7-.9c0-.5.8-.9 1.7-.9z"/>
        </svg>,
        "Ollama"
      );
    case "tavily":
      return wrap(
        <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="10.5" cy="10.5" r="6.5"/>
          <path d="M15.5 15.5L21 21" strokeLinecap="round"/>
        </svg>,
        "Tavily"
      );
    default:
      return wrap(
        <span className="inline-flex items-center justify-center rounded bg-slate-800 text-[10px] font-bold text-slate-300 uppercase px-1 py-0.5 border border-slate-700">
          {vendor.slice(0, 3)}
        </span>,
        providerLabel
      );
  }
}

/**
 * A provider's mark next to its name — what the providers tab lists and what a provider select
 * shows while it is open. Once one is chosen, only the mark stays (the `nameless` variant).
 */
export function ProviderBadge({
  provider,
  nameless = false,
  className = "w-4 h-4",
}: {
  provider: string;
  nameless?: boolean;
  className?: string;
}) {
  const name = PROVIDER_NAMES[provider] || VENDOR_NAMES[normalizeVendor(provider)] || provider;
  return (
    <span className="inline-flex items-center gap-1.5 min-w-0">
      <ProviderIcon provider={provider} className={`${className} flex-shrink-0 text-slate-300`} />
      {!nameless && <span className="truncate">{name}</span>}
    </span>
  );
}

/** The name a person reads for a model: "Fabricante: Modelo", and what an alias points to today. */
export function fullModelName(name: string, id: string): string {
  return name && name !== id ? name : id;
}
