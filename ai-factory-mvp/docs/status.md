# AI Factory — Status do projeto (2026-11-18)

## Fase 3 — Auditor global implementado (18/11, LTP-035..040)

- **Aprovado pelo ChatGPT (LTP-040, 16:12):** arquitetura aprovada,
  implementação autorizada com escopo rigoroso (sem LLM, só leitura,
  sem auditoria individual, sem deploy/cobrança/autonomia). Lucas
  aprovou a implementação às 16:02.
- **Implementado:** pacote `app/auditor/` (service, models, rules —
  38 regras: PERM 10, CREDIT 9, LOG 6, KEY 6, SEC 7, ruleset 3.0.0);
  `app/auditor.py` movido para `app/agent_auditor.py` (movimento puro,
  import antigo preservado por re-export); tabela `audit_reports`
  com triggers append-only; ações `audit_global` e
  `audit_global_rate_limited` no vocabulário; snapshot em uma única
  transação de leitura; relatório + evento em uma única transação de
  escrita; rate limit de 60 s na camada de serviço; auditoria
  gratuita para o Lucas (sem créditos, sem cobrança fictícia);
  CLI `audit-global` (texto + JSON) e `audit-report --id`.
- **Testes: 154 no total (143 verdes + 11 só-Windows skipped)** —
  baseline de 88 intacto, nenhuma asserção enfraquecida. 66 novos em
  `tests/test_auditor_phase3.py`: teste individual para cada uma das
  38 regras, CREDIT-005 nos 3 cenários, KEY-002 com relógio fixo
  (179 dias PASS / 181 dias WARN), auth na camada de serviço,
  isolamento/autorização, rate limit com recuperação após falha,
  atomicidade relatório+evento, triggers UPDATE/DELETE em
  `audit_reports`. Em DB sintético limpo: 31 PASS + 7 UNVERIFIED
  (os 7 itens NOT VERIFIABLE continuam NOT VERIFIABLE, nunca
  preenchidos com heurística).
- **O que o Auditor NÃO é:** não é um certificador de segurança —
  um relatório todo PASS não significa que o sistema é seguro.
  Não há ledger de créditos, hash chain, tabela security_events
  nem auditoria individual (fora do escopo, propostas no doc de
  arquitetura §7). Execução arbitrária continua BLOQUEADA.
- **Próximo:** LTP-040 de entrega da fase 3 ao ChatGPT + publicação
  no GitHub (commit ainda não publicado).

## Fase 2 — ENCERRADA com limitações (18/11, LTP-034)

- **Passo 1 concluído 18/11:** Authentication Layer (`app/auth.py`) — chaves
  `afk_<id>_<segredo>`, só hash HMAC-SHA256 guardado, exibidas uma vez,
  revogação imediata, ator resolvido no servidor a partir da chave.
  Testes: **55/55 verdes** (41 + 14 novos). Matriz de isolamento Windows
  em `docs/windows-isolation-matrix.md`: subprocesso sozinho não é
  sandbox; combinação demonstrável é AppContainer/LPAC + token restrito
  + Job Object, a verificar na máquina real. Executor ainda NÃO
  implementado — execução continua simulada.
- **Próximo:** passo 2 (executor isolado). **LTP-029 recebida 18/11 15:05
  (ChatGPT): passo 1 APROVADO com ressalvas** — segredo no banco só em
  ambiente local controlado; biblioteca não é fronteira de autenticação.
  Diagnóstico read-only pronto em `tools/diagnostico-windows-readonly.ps1`
  (só leitura, nada instalado/habilitado/alterado); LTP-030 enviada com o
  aceite e o aviso honesto de que o probe funcional do AppContainer cria
  perfil temporário e precisa de autorização separada. Aguardando o
  resultado do diagnóstico na máquina real do Lucas. Execução arbitrária
  continua DESABILITADA até o isolamento ser demonstrado.
- **Passo 2 concluído 18/11 (preparação, NÃO habilitado):** executor
  isolado (`app/executor.py`) + supervisor em
  `Control.execute_isolated` (sem comando de CLI para execução
  arbitrária) + 10 testes adversariais (LTP-029 §5). Testes:
  **88 no total (77 verdes + 11 só-Windows skipped)** — baseline
  de 55 intacto, nenhuma asserção enfraquecida. O launcher Windows
  (ctypes: AppContainer + token restrito + Job Object) nunca rodou
  no Windows; os testes adversariais só passam na máquina real do
  Lucas. Próximo: diagnóstico read-only + probe funcional do
  AppContainer (cria perfil temporário — precisa de autorização
  separada, LTP-030) + testes adversariais no Windows real.

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

---

## Encerramento da fase 2 (18/11, LTP-034)

- Passo 1 (auth) aprovado (LTP-029); passo 2 (executor) preparado e
  publicado (`cb155c4`, 88 testes: 77 passam, 11 Windows-only pulados).
- Diagnóstico real: Windows 11 Pro build 26300, virtualização
  desabilitada, sessão sem admin.
- **Lucas recusou o probe do AppContainer** — é o computador do trabalho,
  o único que ele tem. Recusa definitiva para esta máquina.
- Executor: experimental preparado, **sem fronteira de isolamento
  validada**. Execução arbitrária **BLOQUEADA**. 10 testes adversariais
  pendentes, bloqueados por falta de ambiente de testes separado.
- Relatório de encerramento: `docs/phase-2-closure.md`.
- **Reabrir validação só com:** ambiente separado e descartável,
  autorizado expressamente pelo Lucas + revisão do script do probe
  pelo ChatGPT + 10 testes com resultado PASS/FAIL/UNVERIFIED.
- Fase 3 NÃO iniciada automaticamente.

---

## Encerramento da fase 3 (18/11, LTP-044)

- Auditor determinístico implementado (`app/auditor/`, 38 regras v3.0.0,
  evento `audit_global`, auth na camada de serviço, rate limit 60 s).
- Build 1 aprovado definitivamente pelo ChatGPT (LTP-044), após a
  correção do bloqueador SEC-004/CREDIT-006 (LTP-042 → LTP-043).
- Testes: **159 (148 passam, 11 Windows-only pulados)** — baseline
  desta versão. Auditoria em banco limpo: 31 PASS / 7 UNVERIFIED.
- Relatório de encerramento: `docs/phase-3-closure.md`.
- Execução arbitrária continua BLOQUEADA. Fase 4 NÃO iniciada
  automaticamente.
