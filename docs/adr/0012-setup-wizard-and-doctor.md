# ADR-0012: `loompa setup` and `loompa doctor` — one checklist of every service a factory needs

Date: 2026-09-20 · Status: accepted (improves the onboarding of ADR-0002 addendum / Fase 0b)

## Context

Onboarding asked for the keys of the chosen preset, then Tavily, and tested them all at the end.
Nothing said what the other pieces were: OpenCode (Fase 2) and the GitHub CLI (the Deployer's
`gh pr create`) were configured elsewhere or not at all, a wrongly pasted key was found out only at
the end, and there was no way to ask "is it all still working?". One real gap sat underneath:
`OpenCodeWorker` runs `opencode` as a subprocess with the parent's environment, and a key stored by
`loompa providers set-key` lives in a file that process never reads.

## Decisions

### 1. One list, read by everyone

`config/services.py::collect_services(config, secrets)` returns a `Service` per thing the factory
uses: each provider a tier names (required), Tavily and any other MCP server with a key (optional),
OpenCode (required only when it is the Worker backend), the GitHub CLI logged in (optional) and the
CodeRabbit CLI (only when enabled). Each carries a pt-BR purpose, a state and what to do about it. It is
a pure function of the config, the secrets and two probes (`which`, `gh_logged_in`) tests replace, and
never carries a key value. The wizard and the doctor both read it, so they cannot disagree.

### 2. `loompa setup`: four steps, re-runnable, asks only for what is missing

Models (numbered preset menu, then each key) → web search → who writes the code (built-in Worker or
OpenCode) → GitHub → the checklist. `loompa init` ends in it. For every key: what it is for, where it
is created (and an offer to open the page), a hidden prompt, and a connection test right away. A key
the provider refuses is wiped and asked again (three tries); a failure that is not the key's fault
(rate limit, network) keeps it. A key already in place is kept without asking. OpenCode that is not
installed says how to get it and leaves the built-in Worker in use, rather than saving a backend
that would crash the first story. The GitHub step offers `gh auth login`.

### 3. `loompa doctor`: the same list, no questions

A connection test per key (`--no-test` skips it); exit code 1 when a required service is missing or
its key is refused, so it works in a script or before a night run.

### 4. OpenCode receives the key of its model, and only that one

`OpenCodeWorker._child_env` adds the API key of the provider its model belongs to (same variable
name as in Loompa's config) to the subprocess environment. Not the others: the agent runs shell
commands and should not hold keys it has no use for.

## Consequences

* The dashboard settings screen is unchanged: it still shows providers and Tavily. The checklist is
  not in its API yet.
* **Not verified against a real OpenCode install** (none on the machine that built this; tests use a
  fake binary). It is an assumption that OpenCode reads the key from the same variable name as
  Loompa's config, which holds for OpenRouter, DeepSeek, Groq and Anthropic by its provider
  convention. Gemini is not handled: OpenCode names that provider and its variable differently, so an
  OpenCode Worker on a Gemini model still needs the key exported in the shell.
* The wizard prompts through `typer`; scripted use stays `loompa init --yes` plus
  `loompa providers set-key`.
