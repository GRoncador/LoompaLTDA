# 14.3 — O que a comunidade testou, aplicado aos prompts do Loompa

06/10/2026 · proposta para a revisão do Founder · **nenhum prompt do código mudou.**

| Arquivo | O que é |
| --- | --- |
| este documento | a análise: referências, padrões, crítica da proposta anterior e a proposta |
| [PROMPTS-14.3.md](PROMPTS-14.3.md) | cada prompt editado: o atual e o proposto inteiros, o diff, a referência e o porquê (gerado) |
| [make_candidates.py](make_candidates.py) | gera os candidatos e confere o contrato JSON, os marcadores do dry-run, o `{language}` e as palavras de produto |
| `candidates/` | `<prompt>.p0-agnostic.txt`, `<prompt>.<ideia>.txt` (P0 + uma ideia, para medir sozinha) e `<prompt>.proposed.txt` (P0 + as ideias recomendadas) |
| [run_round3.sh](run_round3.sh) | a rodada da bancada que mede tudo isso; **ainda não rodou** |

## Resumo

- Li as skills do Matt Pocock e mais 14 referências: os guias de prompt da Anthropic, da OpenAI e
  do Google, as skills do obra/superpowers e da Anthropic, o GitHub Spec Kit, Specification by
  Example, Example Mapping, INVEST, e dois artigos (juiz LLM e injeção de prompt). **A lista de
  referências que você ia mandar não chegou na mensagem.** Se mandar, eu cruzo com esta.
- Saíram 31 padrões, mais 3 que olhei e recusei. 7 já estão nos nossos prompts. Proponho 10
  ideias (P0 a P10) em 16 prompts, mais 6 textos fora da bancada; o P3 e o P4b ficam só para
  medir. Recuso ou adio 10, com o motivo.
- Dois achados no código, fora do texto dos prompts:
  1. o inbox marca como **recomendada a primeira opção** que um modelo escreve
     (`master.py:945`), mas nenhum prompt diz isso a quem escreve as opções;
  2. o **Inspector lê só os primeiros 3.000 caracteres da spec** (`inspector.py:380`). É a mesma
     classe de corte que a d14806a consertou no Worker.
- Da proposta anterior (P0 a P6):
  - ficam o P0 (agora completo: faltavam quatro prompts), o P4b e três das cinco correções do P6;
  - foram refeitos o P1, o P2, o P5 e duas correções do P6, que criavam contradições novas;
  - saem o P4a e o exemplo do P1;
  - o P3 fica para depois do conserto do Inspector.
- Nossos modelos são MiMo, GLM, DeepSeek e GPT pela OpenRouter, não Claude nem Gemini. Por isso
  dei peso ao que aparece em mais de uma fonte, e deixei de lado o conselho que vale para um
  modelo só (como baixar o tom das maiúsculas, que é do Claude 4).

## 1. O que li

Todas com licença aberta: MIT (Matt Pocock, superpowers, Spec Kit) e Apache 2.0 (skills da
Anthropic). O que entra nos prompts são as ideias, em texto nosso; nenhum trecho foi copiado (14.4).

| Referência | O que é | Por que conta |
| --- | --- | --- |
| [Matt Pocock — skills](https://github.com/mattpocock/skills) | 15 skills: writing-for-agents, code-review, tdd (+tests, mocking), diagnosing-bugs, to-tickets, to-spec, triage (AGENT-BRIEF, OUT-OF-SCOPE), retro, research, grilling, domain-modeling, codebase-design (só o DESIGN-IT-TWICE), pr, implement, handoff | repositório de skills muito usado; o writing-for-agents é sobre escrever para agentes |
| [Anthropic — Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) | o guia oficial de prompts para os modelos atuais | medido pelo fabricante; as partes por modelo foram ignoradas |
| [Anthropic — Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | "altitude certa" do prompt, exemplos canônicos em vez de lista de casos | engenharia de agentes em produção |
| [Anthropic — Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | descrição de ferramenta é prompt | idem |
| [Anthropic — skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) | "explique o porquê", "generalize em vez de remendar o caso", "mantenha enxuto" | o método da Anthropic para escrever e medir skills |
| [OpenAI — GPT-5](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide), [otimizador](https://developers.openai.com/cookbook/examples/gpt-5/prompt-optimization-cookbook), [GPT-4.1](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide), [Codex](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide) | instruções contraditórias, ansiedade do agente, contexto longo, "menos é mais" | medido pelo fabricante; o das contradições vale para qualquer modelo que pensa |
| [Google — Prompt design strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies) e o [whitepaper de Lee Boonstra](https://justbeingresourceful.com/2025/04/15/mastering-prompt-engineering-key-insights-from-googles-must-read-whitepaper/) (resumo) | instrução em vez de restrição, exemplos com formato igual, contexto antes e pergunta no fim | terceiro fabricante, mesmas conclusões |
| [obra/superpowers](https://github.com/obra/superpowers) | verification-before-completion, writing-plans, code-reviewer, systematic-debugging, TDD | biblioteca de skills de agente de código mais usada depois das oficiais |
| [GitHub Spec Kit](https://github.com/github/spec-kit) — `/specify`, `/clarify` | quando marcar dúvida e quando assumir; a opção recomendada em destaque | é o formato de spec que o Loompa já segue |
| [Specification by Example](https://gojko.net/books/specification-by-example/), [Example Mapping](https://cucumber.io/blog/bdd/example-mapping-introduction/), [INVEST](https://xp123.com/articles/invest-in-good-stories-and-smart-tasks/) | critério com exemplo concreto, um comportamento por cenário, história testável | prática de BDD com 15 a 20 anos de uso |
| [Zheng et al. 2023 — LLM-as-a-judge](https://arxiv.org/abs/2306.05685), [Spotlighting (Microsoft, 2024)](https://arxiv.org/abs/2403.14720) | vieses do juiz; dado separado de instrução | artigos com medida |

## 2. Os padrões, um por um

Legenda: **já temos** = o prompt já faz; **P*n*/o*n*** = proposto (ver a seção 4 e o
PROMPTS-14.3.md); **não** = recusado, com o motivo.

| # | Padrão | Fontes | Onde entra no Loompa | Veredito |
| --- | --- | --- | --- | --- |
| 1 | Explicar o porquê de cada regra | Anthropic (add context), skill-creator | todos os prompts ("Why:") | **já temos** |
| 2 | Generalizar em vez de remendar o caso; nada de produto no prompt | skill-creator, context engineering ("altitude certa"), sua regra de 06/10 | 11 prompts citavam tamagotchi, `valor` em reais, OFX, 12.50… | **P0**; faltavam 4 prompts no P0 antigo |
| 3 | Dizer o que fazer, não só o que não fazer | Anthropic, Google, Matt Pocock (Negation) | texto ao Founder; regra do Worker sobre checagens | **P1**, **P5** reescrito |
| 4 | O estilo do prompt passa para a resposta | Anthropic (match prompt style), GPT-4.1 (repetição de frases de exemplo) | o "waiting for the founder" virou "espera pelo fundador" nos relatórios | **P1** |
| 5 | Instruções contraditórias custam raciocínio | OpenAI GPT-5, otimizador da OpenAI | 7 prompts | **P6** (revisto), **P10** |
| 6 | As regras de um conceito juntas, num lugar só | Matt Pocock (co-location, single source of truth) | os gatilhos de `needs_decision` estão espalhados na spec, e é daí que vem a contradição | **P10** |
| 7 | Palavra-guia: um termo que o modelo já conhece no lugar de uma frase | Matt Pocock (leading words) | "tracer bullet", "expand–contract", "scope creep" | **P2**, **P6**, **P3** |
| 8 | Critério de conclusão claro e exigente | Matt Pocock | "For EACH criterion", "EVERY", a autoconferência (DoD) | **já temos** |
| 9 | Cortar o que não muda o comportamento (no-op, sedimento) | Matt Pocock, skill-creator, Codex, Gemini | os prompts longos (Architect 5,2 mil caracteres) | **não agora**: o próprio Matt diz que o no-op se decide rodando, não lendo. Nenhum corte sem bancada |
| 10 | Exemplos few-shot (3 a 5, variados, no mesmo formato) | Anthropic, Gemini, context engineering | texto ao Founder | **não agora**: o P1 antigo, com um exemplo, perdeu e custou 60% a mais; o exemplo teria de vir em pt-BR (regra da casa: o modelo lê inglês) e o GPT-4.1 avisa que o modelo copia a frase do exemplo |
| 11 | Tags XML e delimitadores | Anthropic, GPT-4.1, Gemini | mensagens de usuário | **já temos** o essencial (títulos `##`, cercas de diff, `<external_data>`); reescrever em XML é para Claude e não se paga aqui |
| 12 | Contexto longo: dados primeiro, pergunta no fim | Anthropic, Gemini, GPT-4.1 | laço de ferramentas do Worker | **não agora**: quase todos os nossos contextos têm menos de 20 mil tokens, e a bancada não mede o fim do laço |
| 13 | Baixar o tom (MUST, CRITICAL em maiúsculas) | Anthropic (Claude 4), skill-creator | — | **não**: é do Claude; nossas maiúsculas marcam quantidade (ONE, EACH), não alarme |
| 14 | Persistência e orçamento de ferramentas do agente | OpenAI GPT-5, GPT-4.1, Codex | Worker ("a few reads… then decide"), guarda de laço | **já temos**. O Codex manda agir com premissas em vez de perguntar; o Worker diz "perguntar é mais barato que chutar". Mantive o nosso, que nasceu de custo medido; a tensão é real (1h32 de espera no SP-010) e fica anotada |
| 15 | Não otimizar para o teste, não fixar valor | Anthropic | Worker | **P5** |
| 16 | Testar pela interface; valor esperado independente; mock só na fronteira | Matt Pocock (tdd, tests, mocking), superpowers | Worker (quem escreve o teste); o Inspector já reprova o tautológico | **P7** |
| 17 | Revisão em dois eixos; o que ninguém pediu (scope creep) | Matt Pocock (code-review) | Inspector | **P3**, depois do conserto do contexto |
| 18 | Os code smells de Fowler como achados | Matt Pocock (code-review) | Inspector | **não**: cada achado vira trabalho (Kaizen), estilo ficou de fora de propósito, e o que é mecânico pede lint (padrão 27) |
| 19 | "A spec é visão; a expectativa razoável é requisito" e a lista do que o revisor deixou de julgar | superpowers (code-reviewer) | Inspector | **não**: contradiz a regra de não inventar escopo, e a lista custa tokens sem campo no contrato |
| 20 | Tracer bullet: a primeira fatia atravessa todas as camadas | Matt Pocock (tdd, to-tickets), Pragmatic Programmer | plano do Architect | **P2**, com a exceção da refatoração larga |
| 21 | Cartões como fatias verticais, com bloqueios explícitos | Matt Pocock (to-tickets) | SPLIT, MEETING, CLASSIFY, `depends_on` | **já temos** |
| 22 | Preparar o código primeiro ("make the change easy") | Matt Pocock (to-tickets), Kent Beck | plano do Architect | **não agora**: a revisão do plano pelo PO marcaria a tarefa como escopo inventado ("unsupported"); precisa mudar as duas regras juntas |
| 23 | Bug: laço vermelho, sintoma exato, hipóteses, instrumentar | Matt Pocock (diagnosing-bugs), superpowers (systematic-debugging) | reprodutor do 7.7; bloco de falha do Worker | **o1**, **o2** |
| 24 | Perguntar em rodadas, cada pergunta com a resposta recomendada | Matt Pocock (grilling), Spec Kit (`/clarify`) | Brainstorm; opções ao Founder | **P8**, **P9**, **o3** |
| 25 | Quando perguntar e quando assumir | Spec Kit (`/specify`), Gemini (ambiguity handling) | spec (`needs_decision`); a reunião já faz | **P10** |
| 26 | Critério testável com exemplo concreto, um comportamento por frase | Specification by Example, Example Mapping, Gherkin, INVEST, Spec Kit, AGENT-BRIEF | spec (quem escreve) e revisão da spec (quem revisa) | **P4**; **P4b** como alternativa |
| 27 | Erro mecânico ganha checagem em código, não frase no prompt | Matt Pocock (retro), ADR-0026 | Ops (diagnóstico da fábrica); e esta proposta | **o4**. Apliquei a mim mesmo: "erro engolido por catch largo" (Codex) não virou regra do Inspector, porque é caso de lint |
| 28 | Afirmar só o que foi verificado | superpowers (verification-before-completion), Anthropic (investigate before answering) | EXEC, SUMMARY, SPRINT_REPORT, Inspector, `denies_facts` | **já temos**; o P6 fecha a brecha do `impact` no EXEC |
| 29 | Descrição de ferramenta é prompt | Anthropic (tools), Codex | ferramenta `blocked` | **o3**; revisar o toolbox inteiro fica como próximo passo |
| 30 | Vocabulário do domínio (glossário) | Matt Pocock (domain-modeling, tdd), DDD | bloco dos documentos da empresa (13.2) | **o5** |
| 31 | Dado não é instrução (spotlighting) | Microsoft 2024, OWASP | todos os prompts | **já temos** |

Também olhei e recusei:

- **Raciocínio antes do veredito no JSON** (pôr `reason` antes de `pass`): a evidência é mista,
  e nossos modelos já pensam antes de responder;
- **Chain of Density** (citado na ADR-0034): densidade não é o objetivo para um Founder não
  técnico, e no próprio artigo as pessoas preferiram a densidade do meio;
- **INVEST como palavra-guia**: as regras específicas já cobrem o V, o S, o I e o T.

Os vieses do juiz (Zheng et al.) são assunto da bancada, não dos prompts. O juiz de outro
fabricante e o aviso "Length is not quality" (viés de verbosidade) já estavam no `compare`. Faltava
o viés de posição: o juiz agora lê cada par nas duas ordens, e o veredito que muda com a ordem vira
empate (7b47664).

## 3. A proposta anterior (P0 a P6), revista com rigor

| | O que era | Veredito | Por quê |
| --- | --- | --- | --- |
| P0 | tirar fábrica, história e produto dos prompts | **fica, completado** | Ganhou no SUMMARY (18×13, auditoria 92,5% → 100%) e empatou no EXEC (16×16). Faltavam quatro prompts: o ALIGN ("rejects 10.99 … accept 12.50"), o REVIEW e o Architect (o caso do ponto decimal) e o REVIEW_CARDS (o "você", que não serve se a fábrica for em inglês) |
| P1 | texto ao Founder: alvo positivo + um exemplo + regra nova para histórias só de testes | **refeito** | Eram três mudanças juntas, então a derrota (13×15, +60% de custo) não diz qual delas pesou. O exemplo era um só, em inglês, para uma resposta em pt-BR. Ficou só o alvo positivo, que agora vale também para o EXEC e o SPRINT_REPORT, onde há defeito medido |
| P2 | tracer bullet no plano | **refeito** | Faltava a exceção que o próprio to-tickets faz: a refatoração larga segue expand–contract. O resultado antigo não vale (o juiz lia planos cortados) |
| P3 | Inspector aponta comportamento não pedido | **adiado** | O Inspector lê a spec cortada em 3.000 caracteres, e a seção "fora do escopo" costuma ficar de fora: daria achado falso. E a frase "Changes inside the paths the plan allows are in scope" foi posta para tirar falsos alarmes de escopo. Medir só depois do conserto, e as duas coisas juntas |
| P4a | spec sem exemplo é reprovada | **sai** | Cada reprovação custa uma rodada inteira da spec |
| P4b | spec sem exemplo ganha nota | **alternativa** | Mede-se se o P4 (na escrita) não bastar |
| P5 | Worker não ajusta valor até passar | **reescrito** | Era só proibição. Agora o alvo vem primeiro ("a lógica geral; os testes verificam, não definem"), como no texto da Anthropic |
| P6 | cinco contradições | **3 ficam, 2 refeitas, 3 novas** | A do Architect mandava devolver a história como grande demais, o que contradiz o CLASSIFY ("UMA história, por maior que seja"); virou "lotes de módulos". A do Pre-flight mandava usar `question`, reservado para o irreversível; virou "uma tarefa protege vários usos". A da spec foi para o P10. Novas: o `impact` do EXEC, as buscas do Analyst e o "arquivo existente" do Worker |

A auditoria de contradições acertou os pares graves, mas também apontou falsos positivos: as
prioridades de 1 a 5 com mais de cinco cartões (prioridade pode repetir) e a regra do `retire` na
reunião. Esses ficaram de fora.

## 4. A minha proposta, na ordem em que eu mediria

Os textos inteiros, antes e depois, estão no [PROMPTS-14.3.md](PROMPTS-14.3.md).

| Ordem | Ideia | Prompts | O que deve mover na bancada | Tokens a mais |
| --- | --- | --- | --- | --- |
| 0 | **Conserto do contexto do Inspector** (código, não prompt) | `inspector.py:380` | pré-requisito do P3 | — |
| 1 | **P9 + o3** — a recomendada primeiro | Analyst, Pre-flight, ferramenta `blocked` (+ P10 na spec) | acerta o prompt com o que o inbox já mostra | +18 cada |
| 2 | **P1** — a voz do texto ao Founder | EXEC, SPRINT_REPORT, SUMMARY | auditoria do EXEC (hoje 72%); "fundador" na 3ª pessoa no relatório (hoje 8–10 de 11) | +20 a +88 |
| 3 | **P6** — contradições | Architect, Pre-flight, PROPOSE, REVIEW, EXEC, Analyst, Worker | EXEC: frases de tranquilização sem base (hoje 19 de 36); demais: nota do juiz e decisões iguais às gravadas | 0 a +40 |
| 4 | **P10 + P4** — quando perguntar; critério com exemplo | spec | `needs_decision` por spec; critérios com valor concreto | +174 |
| 5 | **P0** — sem produto | 11 prompts | não piorar (é higiene) | ~0 |
| 6 | **P7 + P5** — testes pela interface; lógica geral | Worker | a bancada vê só a 1ª chamada: medir ao vivo (retrabalho e reprovação de teste tautológico) | +176, no prefixo em cache |
| 7 | **P8** — perguntas em rodada | Brainstorm | nota do juiz | +64 |
| 8 | **P2** — tracer bullet | Architect | só ao vivo: retrabalho por história | +126 |
| 9 | **o1, o2, o4, o5** | reprodutor, bloco de falha do Worker, Ops, glossário | ao vivo; o o2 pede junto uma checagem de `[debug]` no diff | +10 a +69 |
| 10 | **P3** | Inspector | depois do item 0 | +87 |

Três cuidados:

- **Uma ideia por vez.** Cada `<prompt>.<ideia>.txt` é o P0 mais uma ideia, medida contra o P0.
  O `proposed` só entra no código ideia por ideia, depois de cada uma ganhar ou empatar sem
  piorar o custo.
- **O que a bancada não vê.** Para P5, P7 e P2 o efeito está no fim do laço do Worker ou no
  retrabalho, e o `compare` só vê a primeira chamada ou a opinião do juiz sobre o plano. Para
  esses, sugiro um braço A/B ao vivo, como o do esforço (`effort_ab_low_share`), em vez de
  adotar no escuro.
- **Custo.** O maior aumento é no Worker: cerca de 180 tokens num prefixo com 91% a 99% de cache.
  O cache se refaz uma vez e o custo por chamada quase não muda.

## 5. Achado fora dos prompts: o Inspector julga com a spec cortada

O Inspector monta o contexto com:

```python
spec = paths.spec.read_text(encoding="utf-8")[:3000]
```

Os critérios chegam inteiros, por outro caminho. Mas as specs da tamagotchi têm mediana de 8,2
mil caracteres, então o "Spec excerpt" corta a spec no meio de uma palavra e deixa de fora as
bordas, o fora do escopo e as regras. É o mesmo defeito que a d14806a consertou no Worker e no PO,
e o conserto é o mesmo:

```python
fit_sections(spec, 8000, SPEC_FOR_REVIEW)
```

Há cortes parecidos nas specs das histórias de que esta depende (`product.py:74`, 6.000) e na
pesquisa (`analyst.py:149`, 5.000). Não mexi em nada disso: fica para você decidir.

## 6. Próximos passos

1. Você revisa o [PROMPTS-14.3.md](PROMPTS-14.3.md) e risca o que não quiser.
2. Eu conserto o contexto do Inspector (seção 5), se você aprovar.
3. Rodar a bancada:

   ```
   docs/bench/run_round3.sh
   uv run python docs/bench/summarize.py ~/.loompa/bench/logs/r3
   ```

   O `summarize.py` agora conta também, por resposta, três coisas:
   - o Founder tratado na 3ª pessoa ou com gênero suposto;
   - frases de tranquilização;
   - números de tarefa ("T1"),

   e, na spec, quantos critérios têm valor concreto. A rodada 2 custou centavos por comparação no
   tier 2.
4. Passa para o código só o que ganhar, uma ideia por commit. Os marcadores do dry-run e o contrato
   JSON já são conferidos pelo gerador.
