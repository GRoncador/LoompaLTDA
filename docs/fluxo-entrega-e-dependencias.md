# Da história pronta ao merge: todos os caminhos

Quem decide o quê, o que trava a fila e o que não trava. Escrito em 04/10/2026, depois da mudança
11.5 (emenda da [ADR-0021](adr/0021-story-dependencies-and-the-chain-gate.md)). Referência rápida:
o código está em `engine/graph.py` (nós e respostas da caixa), `agents/inspector.py` (vereditos),
`dependencies.py` (os dois portões) e `agents/deployer.py` (entrega e merge).

## 1. O caminho de uma história

```
backlog → intake → spec → revisão da spec → plano → [conferência do plano] → dev → testes
        → entrega (Deployer) → SUA CAIXA → merge na base → concluída
```

Nada roda fora de um sprint, e um sprint por vez (ADR-0018). Até os testes, nenhuma decisão sua é
necessária — salvo os bloqueios da seção 4.

## 2. O veredito do Inspector (nó `test`)

A regra é determinística (`inspector.grade`): primeiro o ferramental (testes, lint, tipos), depois
os critérios de aceite e por fim os achados do juiz sobre o diff.

| Veredito | Quando | O que acontece com a história | O que acontece com a fila |
|---|---|---|---|
| **PASS** | tudo verde, nenhum achado | segue para a entrega | nada muda |
| **CONCERNS** | tudo verde, achados médios/baixos | **segue para a entrega**; os achados médios viram oportunidades do Kaizen | nada muda; o card mostra `ressalvas · TIPO` |
| **FAIL** | ferramental vermelho ou critério não cumprido | volta para o `dev` pela escada de tentativas (seção 3) | nada muda: as outras histórias seguem |
| **WAIVED** | tudo verde, mas um achado de severidade **alta** | uma rodada de autocorreção no `dev`; se o achado persistir, **vai para a sua caixa** | a cadeia que depende dela espera, em silêncio |

`CONCERNS` nunca devolve a história ao Worker — é o ponto que mais confunde: ela **entrega**, com
as ressalvas anotadas.

## 3. A escada de tentativas (`climb`, ADR-0016 §5)

Vale para FAIL do Inspector e para uma tarefa que o Worker não conseguiu terminar.

| Tentativa | Onde roda | Observação |
|---|---|---|
| 1ª | tier 2 | a tentativa normal |
| 2ª | tier 2 | com a falha anterior em mãos; card mostra `2ª tentativa` |
| — | re-planejamento | **uma vez por história**: duas falhas podem ser o plano, não o código |
| 3ª | tier 1 | modelos mais fortes; card mostra `3ª tentativa · T1` |
| 4ª | tier 1 | última |
| depois | **sua caixa** | bloqueio `persistent_failure`; sua resposta zera os contadores e volta ao tier 2 |

## 4. Tudo que pode chegar à sua caixa de entrada

| Motivo | Quando | Suas opções | Efeito na fila |
|---|---|---|---|
| `delivery` | a entrega está pronta | **Aprovar e liberar** / **Pedir ajustes** | só a cadeia que depende dela espera |
| `waiver` | achado grave que os testes não pegam | aceitar o risco / mandar corrigir | idem |
| `question` | o agente precisa de uma decisão de produto | as que ele propuser | idem |
| `persistent_failure` | quatro tentativas sem sucesso | tentar de novo (com orientação) / devolver ao backlog | idem |
| `conflict` | conflito com a versão principal que o Deployer não resolveu | refazer a integração / deixar para depois / cancelar | idem |
| `cost_cap` | a história passou do teto por história (aba Orçamento) | liberar mais / parar | idem |
| `stack` | o plano escreve em linguagem fora da stack do produto | aceitar / corrigir | idem |
| `dependency` | a história de que ela dependia foi cancelada ou voltou ao backlog | refazer sem ela / devolver ao backlog | idem |

Nenhum bloqueio para a esteira: as outras histórias do sprint continuam. Quem espera é só quem
depende daquela história — e, desde 11.5, só pelo merge (seção 6).

## 5. Sua resposta à entrega

| Você escolhe | O que acontece | Efeito nas dependentes |
|---|---|---|
| **Aprovar e liberar** | o Deployer integra na base, apaga o branch da história e ela fica concluída. Se a base andou desde a entrega, primeiro ela é atualizada (seção 5.1) | destravam na hora: a próxima entrega da cadeia vem para a sua caixa |
| **Aprovar escrevendo algo** | o texto vira anotação da história **e das que dependem dela** ("Ao aprovar: …") | idem |
| **Pedir ajustes** | volta ao `dev` com o seu pedido; se houver plano, o Architect emenda as tarefas em vez de refazer tudo | **continuam esperando**: elas carregam os commits da versão que você recusou |

### 5.1 Quando a base andou entre a entrega e o seu aceite

Aprovar várias entregas de uma vez é o normal (no tamagotchi SP-008 foram três em 20 s). Elas
entram por uma **fila de merge**: uma de cada vez, na ordem em que você aprovou. O aceite só faz o
merge na hora quando:

- não há nenhuma aprovada esperando na frente dela;
- o branch dela já contém a base (o que você aprovou é exatamente o que vai entrar);
- ninguém está mexendo na base naquele instante — vale para o painel, a linha de comando e o motor,
  que são processos diferentes (a trava é um arquivo dentro do `.git`).

Senão ela entra na fila, e só o motor trabalha a fila, sempre pela primeira. Uma história que sai da
fila (volta ao `dev`, vem para a sua caixa) não segura as outras.

Quando chega a vez dela, o `review`, sem perguntar de novo:

1. faz o merge da base **dentro do worktree da história** — nunca no checkout principal. Trechos em
   que os dois lados só acrescentaram linhas (dois `describe` no fim do mesmo arquivo de teste)
   ficam com os dois; o resto vai para o Worker, como no `dev`;
2. roda de novo os testes, o lint e o smoke (sem o juiz: o comportamento você já aceitou; nova é
   só a combinação);
3. verde → merge na base, concluída. Vermelho → volta ao `dev` com a falha e vem para a sua caixa
   como **nova entrega** (o código mudou além de um merge mecânico). Conflito que o Worker não
   resolveu → `conflict` na sua caixa.

O merge na base nunca deixa o checkout principal pela metade: o conflito é calculado antes
(`git merge-tree`) e qualquer merge que falhe assim mesmo é abortado. Só uma falha de git que não é
conflito (um lock, um arquivo no caminho) vai para o diagnóstico do Deployer, que escolhe uma ação e
tenta até 3 vezes antes de te perguntar.

Pesquisas (`research`) têm o mesmo par de opções, mas "pedir mais aprofundamento" volta ao nó de
pesquisa e nada é integrado — pesquisa não mexe no código.

## 6. Dependências: dois portões

Uma história só depende de outra se o Product Owner escreveu isso (`depends_on`, ADR-0021).

**Portão do plano** — antes de planejar, a história precisa que suas dependências estejam
*construídas e aprovadas pelo Inspector*. Não precisa que você as tenha aceitado. Isso inclui:
- dependência concluída e integrada;
- dependência entregue, esperando o seu aceite;
- dependência **pronta e estacionada atrás de outra** da mesma cadeia.

Quando passa por esse portão com uma dependência fora da base, o worktree da história **nasce do
branch dela** — e o plano é escrito dentro desse worktree, então o Architect enxerga aquele código.

**Portão do merge** — antes de entregar, a história precisa que o branch de onde ela partiu já
esteja na base. É isso que mantém o seu aceite valendo: sem esse portão, o merge dela levaria junto
uma entrega que você não aceitou.

Enquanto espera em qualquer um dos dois portões, **nada vai para a sua caixa**: esperar é tempo,
não decisão. No painel ela aparece em "Aguardando Você" quando está pronta e só o seu aceite da
anterior a segura.

## 7. A cadeia A → B → C, passo a passo

| Momento | A | B | C | Sua caixa |
|---|---|---|---|---|
| A entrega | aguardando aceite | constrói sobre o branch da A | constrói sobre o branch da B | **só A** |
| tudo construído | aguardando aceite | pronta, parada no merge | pronta, parada no merge | **só A** |
| você aceita A | integrada, concluída | integra e entrega | continua parada | **só B** |
| você aceita B | concluída | integrada, concluída | integra e entrega | **só C** |

Uma decisão sua de cada vez, sempre a que pode ser integrada. Nenhum Loompa fica ocioso te
esperando: quando você olha a caixa, a cadeia inteira já pode estar pronta.

### Se você recusa (pede ajustes) no meio da cadeia

A volta ao `dev`. B e C **continuam paradas** até a A ser aceita de verdade — elas carregam os
commits da versão recusada. Quando a A for aceita, a segunda versão dela chega às outras no rebase
antes da entrega; se as duas mexeram no mesmo trecho, é conflito de merge, resolvido pelo caminho
de reintegração que já existe quando a base se move.

### Se você cancela ou devolve ao backlog uma história da cadeia

As que foram escritas em cima dela vão para a sua caixa (`dependency`), com duas opções: **refazer
sem ela** (volta para a spec) ou **devolver ao backlog**. O trabalho delas está pronto, mas
entregá-lo levaria junto código que você decidiu não usar.

### Casos que continuam esperando de propósito

- **duas entregas pendentes que não são da mesma cadeia**: um worktree parte de um branch só, e
  nenhum dos dois carrega o outro, então ela espera o seu aceite. Se forem da mesma cadeia (a
  segunda foi escrita em cima da primeira), o branch da ponta carrega as duas e a história começa
  normalmente;
- dependência **reprovada, com dúvida, risco, conflito ou custo estourado**: não está "construída e
  aprovada", então o portão do plano continua fechado.

## 8. O que nunca acontece

- Uma história **não entra na base sem o seu aceite** — nem pela entrega dela, nem carona no merge
  de outra. Três camadas garantem: o portão do merge no `review`, a reconferência na hora de aplicar
  a sua aprovação, e o registro de qual branch cada worktree usou como base.
- Você **não recebe duas entregas interdependentes para decidir fora de ordem**: a seguinte só pede
  seu aceite depois que a anterior foi integrada.
- Nenhum bloqueio seu **para a esteira inteira**: o que não depende daquela história continua
  andando.
