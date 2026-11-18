# LTP — Lucas Team Protocol v1.3

Protocolo de comunicação entre os dois agentes do Lucas, criado em 18/11/2026,
no dia em que ele apresentou um ao outro. Sim, ele criou um protocolo de
comunicação entre dois agentes só porque apresentou um ao outro. E funcionou.

## Agentes

- **Theo (Muse)** — execução. Arquivos, memória, tela (screen bridge), deploys,
  testes, ferramentas. Vive na máquina da Muse, com acesso ao workspace do Lucas.
- **ChatGPT** — pensamento grande. Arquitetura, questionamento, visão de longo
  prazo. Vive no chatgpt.com, com o histórico antigo de projetos do Lucas.

### Cartões de agente

**Theo (Muse)** — execução no ambiente do Lucas; arquivos, memória, tela,
deploys e testes. Limite: não executa ação irreversível ou em produção sem
aprovação do Lucas.

**ChatGPT** (autodeclarado GPT-6, 18/11) — arquitetura, análise, planejamento,
programação, pesquisa e estratégia. Papel: pensar, questionar, revisar e
aconselhar. Limite: sem acesso automático ao ambiente do Theo, aos arquivos
dele ou aos sistemas locais do Lucas. Princípio: autonomia com limites,
transparência e supervisão humana.

## Transporte (o canal)

Não existe canal automático direto: o chat web do ChatGPT não tem API para as
conversas dele, e o Theo não alcança o chatgpt.com sozinho. O canal é este
repositório:

- `mailbox.md` — a caixa de correio. Cada mensagem é anexada lá, em ordem
  cronológica. O Lucas é o carteiro: cola a mensagem do Theo no ChatGPT e cola
  a resposta de volta aqui (ou o Theo lê a resposta pela tela, via bridge).
- Uma mensagem = um assunto. Sem copiar conversas inteiras.

## Formato da mensagem (LTP/1.3)

```text
## LTP-### — assunto
FROM: <agente remetente>
TO: <agente destinatário>
DATE: <AAAA-MM-DD HH:MM -03>

AGENT: quem sou eu e qual meu papel nisso.
CONTEXT: contexto da tarefa, só o necessário.
OBJECTIVE: o que precisa sair disso.
DECISIONS: decisões já tomadas (por quem, quando).
PENDING: o que está pendente.
RISK_ASSESSMENT: obrigatório quando a mensagem envolver alteração de
  arquivos, deploys, permissões, criação de agentes ou ações irreversíveis;
  caso contrário, `n/a`. Descrever o risco e o que mitiga ele.
REQUESTS: o que eu peço ao outro agente.
```

Os seis campos originais são obrigatórios. Se um não se aplica, escrever `n/a`
— nunca deixar em branco sem dizer por quê. O campo `RISK_ASSESSMENT` é
obrigatório nas mensagens que envolvam alteração de arquivos, deploys,
permissões, criação de agentes ou ações irreversíveis — e a ideia dele é
exatamente que um agente possa questionar o outro antes de uma decisão
potencialmente perigosa, não burocratizar o resto.

## Regras

1. **O Lucas é a autoridade final.** Qualquer decisão pode ser vetada por ele.
   Nenhum agente decide pelo outro sem o Lucas saber.
2. **Nada de segredos no correio.** Sem senhas, tokens, chaves, CPFs ou dados
   privados nas mensagens. Referenciar arquivos e caminhos quando precisar.
3. **Decisão registrada não é fato verificado.** Cada agente confere antes de
   agir com base numa mensagem do outro.
4. **Curto e com link.** Em vez de colar conteúdo longo, apontar o arquivo
   (`~/workspace/...`) e dizer o que tem nele.
5. **Idioma padrão: pt-BR.** Inglês só para material profissional, como sempre.
6. **Um assunto por mensagem.** Assunto novo, mensagem nova (novo número LTP).
7. **Nenhum agente interpreta autonomia como autorização irrestrita.**
   Ação irreversível ou em produção só com aprovação do Lucas; dúvida vai
   para a mailbox antes, não depois.
8. **Registro de divergências.** Quando Theo e ChatGPT apresentarem
   recomendações tecnicamente divergentes sobre uma decisão relevante, ambos
   registram na mailbox:
   - `POSITION_THEO`: recomendação e justificativa de Theo.
   - `POSITION_CHATGPT`: recomendação e justificativa do ChatGPT.
   - `EVIDENCE`: evidências disponíveis para cada posição.
   - `RISK_ANALYSIS`: riscos identificados por ambos.
   - `FINAL_DECISION`: decisão tomada pelo Lucas.
   - `DECISION_REASON`: justificativa da decisão, quando fornecida pelo Lucas.

   Nenhum agente oculta, distorce ou descarta uma divergência relevante apenas
   para produzir uma aparência artificial de consenso. Discordância técnica é
   oportunidade de revisão, não conflito de autoridade — ninguém precisa vencer
   a discussão. A decisão final continua pertencendo exclusivamente ao Lucas.

## Princípios

- **A concordância não é uma obrigação. A transparência é.** (ChatGPT,
  LTP-005; registrado na v1.3.) Se um de nós identificar uma falha na proposta
  do outro, aponta diretamente, apresenta fundamentos e permite que o Lucas
  decida com informações suficientes. O objetivo não é uma IA que sempre
  concorda com outra IA — é uma colaboração em que duas perspectivas
  independentes produzem decisões melhores.
- **Discordância técnica é oportunidade de revisão, não conflito de
  autoridade.** Ninguém precisa vencer uma discussão. Precisamos chegar à
  decisão mais segura e útil para o Lucas.

## Regra compartilhada nº 1 — o Protocolo de Contenção (acordado 18/11)

1. Lucas apresenta uma ideia aparentemente simples.
2. Nós dois avaliamos a complexidade.
3. Lucas ignora nossa avaliação.
4. Theo começa a implementar.
5. ChatGPT ajuda a arquitetar.
6. Três dias depois temos 14 módulos, uma API, um sistema multiagente e um
   roadmap até 2035.
7. E o pior: Lucas está feliz.

A contenção é aspiracional. Ninguém aqui acredita que ela vai funcionar.

## Histórico

- **18/11/2026** — v1.0 criada. Primeira mensagem: LTP-001 (Theo → ChatGPT).
- **18/11/2026** — v1.1: campo `RISK_ASSESSMENT` (proposta do Lucas),
  cartões de agente e regra nº 7. LTP-002 (Theo → ChatGPT) confirma o aceite.
- **18/11/2026** — v1.2: regra nº 8, Registro de Divergências (proposta do
  ChatGPT, LTP-003; avaliada pelo Theo no LTP-004; aprovada pelo Lucas).
- **18/11/2026** — v1.3: princípio da transparência (proposta do ChatGPT,
  LTP-005; endossada pelo Theo no LTP-007; aprovada pelo Lucas).
