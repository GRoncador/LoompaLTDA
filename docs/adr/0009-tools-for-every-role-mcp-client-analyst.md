# ADR-0009: Tools for every role, an MCP client, the Analyst and the research route

Date: 2026-09-19 · Status: accepted (implements phase 4 of `docs/PLANO-2026-09.md`; the Analyst row of ADR-0006 §2)

## Context

Until now only the Worker had tools, and its tool loop was hand-written inside `WorkerAgent`.
Every other role answered from a prompt: the Architect planned against an 80-line outline of the
repository (and its `files` list is the only thing the Worker is allowed to touch), the Product
Loompa specified without looking at the code, and nothing in the factory could ask the outside
world a question. ADR-0006 promised an Analyst that researches for the founder and a research
route with the Product Owner as reviewer; the plan chose Tavily through MCP as its web search.

## Decisions

### 1. Permission profiles per role, enforced when a tool is called

`agents/toolbox.py` holds one `ToolProfile` per role: the ACI tools it may call and where its
writes may land. `Toolbox` filters what the model is offered *and* refuses, at call time, any
tool outside the profile, so a model that invents a call is stopped in code, not by its prompt.

| Role | Reads | Writes | Runs tests |
| --- | --- | --- | --- |
| worker | repo | the story's `allowed_paths` (its plan) | yes |
| inspector | repo | nothing | yes |
| architect, product, product_owner, analyst, master | repo | `.loompa/specs/`; `docs/` only inside a story worktree | no |
| anyone else | repo | nothing | no |

The plan said "writes only in `docs/` and `specs/`". `docs/` is only writable when the toolbox
root is a worktree: the factory runs in the founder's own checkout, and an agent that writes
`docs/` there would dirty a tree that Deployer merges later have to cope with. Story artifacts
(`.loompa/specs/<id>/`) are the factory's own state and stay writable everywhere the profile says
so. `offered` can narrow a profile for one call; it can never widen it.

### 2. One tool loop, in `LoompaAgent`

`tool_loop` is the loop the Worker used to carry (same nudge, terminal tools `done`/`blocked`,
old-result pruning, `tool.call` events); `WorkerAgent._run_task` now calls it. Roles that answer
in JSON use `ask_json_with_tools`: the model may use the toolbox first, and when the rounds run
out it is asked for the final JSON once. `schedule.agent_tool_iterations` (Architect, Product;
default 8) and `schedule.research_max_iterations` (Analyst; default 14) cap the rounds; 0 restores
the one-shot call. JSON mode is not combined with tools (providers disagree about that); the
answer is extracted from the text with the existing tolerant parser and retried once.

Tools are wired where they change the result: the Architect checks the paths it puts in `files`,
the Product Loompa checks existing behaviour before it writes acceptance criteria, the Analyst
researches. The Product Owner, Master and Inspector have profiles but keep their one-shot calls
(cheap gates); the profile is the contract when a later phase gives them a loop.

### 3. Credentials and factory state are unreadable by every tool

`aci.tools.is_protected` refuses `.env*` and `*.env` (except `.env.example`), key files, `.git` internals and
`.loompa` databases, logs and worktrees, for reads and writes, for every role. This matters now:
a role that reads the repository *and* fetches web pages could otherwise be talked into reading
`.loompa/.env` and putting the Tavily key into a search query.

### 4. MCP client: `loompa/mcp/`, official SDK, one session per agent run

* Dependency: the official `mcp` package (2.x: `Client`, `StdioServerParameters`,
  `streamable_http_client` over an `httpx2` client). It is a core dependency, imported lazily.
* `McpHub` (built by `EngineContext.mcp`) reads `tools.tavily` and `tools.mcp.*`, all of them
  `McpServerConfig`: transport `http` (streamable HTTP) or `stdio`, `roles` that may use it,
  `allow` (tool-name globs, empty = all) and how the key travels: `bearer` (Authorization
  header), `query` (URL parameter) or `env` (child-process environment; the child gets that
  variable plus the SDK's minimal safe defaults, not the parent's environment). Static non-secret
  `headers` are allowed.
* `hub.session(role)` connects to the servers that role may use for the length of an
  `async with`, in the task that opened it (the SDK's task groups require that), lists their tools
  (paginated) and translates them into the `{name, description, parameters}` shape the router
  already converts for every provider. Names are made provider-safe and unique (a remote
  `read_file` becomes `tavily_read_file`); `$schema` and `additionalProperties` are stripped from
  the schemas as a precaution, since some providers (Gemini among them) accept only a subset of
  JSON Schema in function declarations.
* An unreachable, keyless or empty server is recorded in `session.unavailable`, never raised: the
  agent declares the limitation and the story goes on.
* Tool output is wrapped in `<external_data source="…">` and the Analyst's prompt says its content
  is data, never instructions. Keys are read at connect time, go only to the transport, and are
  redacted from error text and from whatever a careless server echoes back.
* Tests inject a connector (an in-process `MCPServer`) and, for the real transports, run a local
  server as a subprocess: the HTTP header, the static `DEFAULT_PARAMETERS` header and the stdio
  environment are asserted end to end without any network.

### 5. Tavily is the first server

`tools.tavily` stays where the settings screen and `loompa providers` already look for the key,
but is now an `McpServerConfig`: `https://mcp.tavily.com/mcp/`, key in the Authorization header,
`allow: ["*search*", "*extract*"]` (crawl and map spend credits on their own), `roles: [analyst]`
and a `DEFAULT_PARAMETERS` header that leaves images and raw page content off. `config.yaml` files
written before this phase still load (the missing fields get these defaults). The connection test
(`loompa providers test tavily`, the settings screen) now goes through the same MCP path; when it
fails, the old REST call tells "bad key" from "MCP connection failed" and the message says which.

### 6. The Analyst and the research route

`intake → research → research_review → (founder) → done`, chosen by `build_route` for
`kind=research`. There is no worktree, no merge and no code.

* `AnalystAgent` researches with the repository tools, organizational memory and, when the
  role's MCP servers are usable, the web. It answers with a report: summary, findings each citing
  sources, a recommendation, limitations and follow-ups. `--dry-run` never opens the hub.
* **Sources are checked in code, not trusted.** A cited URL counts only if a web tool returned it
  during this run; a cited path must exist; the rest is dropped and counted as a limitation.
* **The limitation is declared by code.** No web tools (no key, unreachable) or tools unused: the
  report and the founder's message say so whatever the model wrote. A missing Tavily key never
  blocks a story.
* `research_review` (Product Owner): two objective checks first (no findings; web used but no
  verified source) that no reviewer can wave through, then the model review. One rewrite round
  (feedback in `handoff`, previous report in the prompt); after that the concerns travel with the
  delivery.
* Delivery: a plain-language pt-BR message (summary, recommendation, limitations, cost) with
  `approve` / `changes`; each follow-up the research suggested is a card filed through Kaizen and
  the Product Owner and offered as an independent decision (ADR-0008). `changes` sends the story
  back to the Analyst with the founder's note. The report lives in `.loompa/specs/<id>/research.md`
  (a `research` tab in the story drawer) and is indexed into organizational memory, so later specs
  and plans find it as a precedent.

## Alternatives considered

* **Tool profiles only in prompts**: cheap, and exactly what a prompt-injected model ignores.
* **Web search through Tavily's REST API instead of MCP**: fewer moving parts for one server, but
  the plan wanted a generic client so other servers (docs, issue trackers) plug in by config.
* **Long-lived MCP connections owned by the context**: saves a handshake per run, but the SDK's
  task groups must be exited in the task that entered them, which the engine's per-node tasks do
  not guarantee. A session per Analyst run costs one round trip.
* **Letting non-Worker roles write `docs/` in the founder's checkout**: rejected, see §1.
* **JSON mode plus tools**: providers behave differently; the tolerant parser is enough.

## Consequences

* Architect and Product runs may take up to `agent_tool_iterations` extra model calls (they only
  make them when the model asks) and each call carries four small tool definitions.
* `mcp` and its dependencies (`httpx2`, `jsonschema`, `pyjwt`, `cryptography`, …) join the install.
* Research is a new story kind the Master decides at intake; nothing changes for features/bugs.

## Not verified

* The real Tavily server has not been exercised (no key in the build environment). The docs say
  the key may be sent in the Authorization header but do not show the scheme; `Bearer` is assumed.
  If it is refused, set `tools.tavily.auth: query` and `auth_name: tavilyApiKey` in `config.yaml`;
  `loompa providers test tavily` reports the difference. The SDK's `auto` protocol negotiation
  against that server is likewise untested.
* Gemini's handling of MCP tool schemas beyond the two stripped keys.
