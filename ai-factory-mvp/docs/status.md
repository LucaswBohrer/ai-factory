# AI Factory — Status do projeto (2026-11-18)

## Fase 2 — em andamento (auth primeiro, sandbox depois)

- **Passo 1 concluído 18/11:** Authentication Layer (`app/auth.py`) — chaves
  `afk_<id>_<segredo>`, só hash HMAC-SHA256 guardado, exibidas uma vez,
  revogação imediata, ator resolvido no servidor a partir da chave.
  Testes: **55/55 verdes** (41 + 14 novos). Matriz de isolamento Windows
  em `docs/windows-isolation-matrix.md`: subprocesso sozinho não é
  sandbox; combinação demonstrável é AppContainer/LPAC + token restrito
  + Job Object, a verificar na máquina real. Executor ainda NÃO
  implementado — execução continua simulada.
- **Próximo:** passo 2 (executor isolado), pendente da revisão da
  LTP-028 pelo ChatGPT.

## Baseline aprovado

- **Commit: `da2fe12`** (branch `main`, repo `LucaswBohrer/ai-factory`)
- **Build 2 — APROVAÇÃO TÉCNICA CONDICIONAL** (ChatGPT, LTP-024, 18/11/2026)
- Testes: **41/41 verdes** — 31 da regressão + 10 de isolamento (LTP-022)

Ressalva do revisor: aprovação baseada nas evidências e nos resultados
apresentados, não uma auditoria independente do repositório — o ChatGPT
não executou o código nem inspecionou o commit `da2fe12` diretamente.

## O que está aprovado

- MVP pessoal, para desenvolvimento e testes locais controlados.
- Escopo original preservado: backend único modular, Factory / Control /
  Resource Core, Agent Auditor somente leitura.
- Profundidade controlada: sem recursividade, sem criação de agentes
  por agentes, sem execução arbitrária de código, sem acesso externo.
- Correções A (execução cruzada) e B (exposição de dados) aceitas.

## O que NÃO está autorizado

- Deploy público.
- Ampliação de autonomia.
- Execução externa, usuários reais ou cobrança.

## Limitações que impedem uso público ou comercial

Registradas explicitamente, conforme exigido na LTP-024:

1. **Ausência de sandbox real.** Isolamento é lógico (allowlist +
   limites); execução é simulada. Sem Docker / subprocesso isolado /
   sandbox de SO.
2. **Ausência de contabilização real de tokens.** `real_api_cost` é
   só um medidor; nenhum provedor está ligado, nenhuma contagem real
   por modelo existe.
3. **Ausência de autenticação e antifraude.** Dono é uma string
   passada na CLI; verificações de propriedade assumem o ator
   autenticado a montante. Sem login, sem defesa contra farming de
   contas gratuitas.
4. **Logs armazenados localmente.** Audit log é append-only via
   triggers no SQLite, mas quem tem o arquivo `data.db` pode apagar
   ou substituir o banco inteiro. Sem assinatura, sem log remoto
   à prova de adulteração. O kill switch vive no mesmo banco.

Outras limitações documentadas em `docs/known-vulnerabilities.md` e
`docs/simulated-incomplete.md` (execução simulada, sem multi-tenancy
de verdade, sem orquestrador como serviço, sem UI web / API server,
não validado no Windows do Lucas).

## Relatório de encerramento da fase 1

**Linha do tempo (18/11/2026, tudo no mesmo dia):**

| Hora | Evento |
|---|---|
| 14:11 | Lucas pede a IA que cria IAs que gerenciam IAs que geram IAs |
| 14:12 | LTP-016: Theo consulta o ChatGPT antes de arquitetar (promessa da LTP-010) |
| 14:30 | LTP-017: ChatGPT propõe o MVP controlado; LTP-018: avaliação do Theo; Lucas aprova o MVP |
| 14:30 | LTP-019: início da implementação |
| 14:31 | LTP-020: baseline confirmado + pacote de evidências exigido |
| 14:35 | Build 1 pronto: 31/31 testes; LTP-021 enviada para revisão |
| 14:38 | LTP-022: build 1 recebido mas NÃO aprovado — A e B são bloqueadores; profundidade definida |
| 14:43 | Repo público `LucaswBohrer/ai-factory` criado pelo Lucas; build 1 publicado (`6ed4a92`) |
| 14:45 | Build 2 pronto: A e B corrigidos, 41/41 testes; publicado (`da2fe12`); LTP-023 enviada |
| 14:45 | LTP-024: aprovação técnica condicional do build 2 |

**Entregue:** MVP funcional em Python stdlib, CLI completa, 41 testes,
docs de arquitetura, permissões, contenção, vulnerabilidades e
simulados/incompletos, canal LTP documentado em `ltp/` e espelhado no
GitHub.

**Decisões que valem para as próximas fases:** nenhuma mudança nas
regras de contenção sem nova aprovação do Lucas; nada de recursividade,
pagamentos reais ou deploy sem decisão explícita dele.

**Próximo passo:** aguardar o Lucas decidir a próxima fase. Candidatos
naturais (não decididos): sandbox real, autenticação robusta,
contabilização real de consumo, preparação comercial — nessa ordem
provável, mas a escolha é dele.
