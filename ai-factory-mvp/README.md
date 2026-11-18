# AI Factory MVP — a IA que cria IAs (versão controlada)

Aprovado em 2026-11-18 por Lucas (LTP-017 + LTP-018, aprovação registrada
na LTP-019 em `~/workspace/ltp/mailbox.md`). Backend único modular,
**Python stdlib only** — sem dependências externas, sem chamadas de rede,
sem chamadas a API de LLM no MVP.

## O que o MVP faz

- **Factory** (`app/factory.py`) — criação de agentes **a partir de templates**
  (`auditor`, `summarizer`, `researcher`). Sem geração por LLM.
  Toda criação exige aprovação humana explícita (`--approve`).
- **Control** (`app/control.py`) — registro, suspensão/kill de agentes,
  log de auditoria **append-only** (triggers no SQLite bloqueiam UPDATE e
  DELETE), kill switch (só o Lucas liga/desliga; quando ON, bloqueia
  criação e execução), execução simulada e limitada.
- **Resource Core** (`app/resource_core.py`) — contabilidade de créditos
  internos. Consumo real de APIs é registrado num campo separado
  (`real_api_cost`), independente dos créditos internos.
- **Agent Auditor** (`app/agent_auditor.py`, era `app/auditor.py`) — primeiro agente, **somente leitura**.
  Analisa config, permissões, riscos, ciclos de execução e consumo de um
  agente e devolve achados com severidade `info` / `warning` / `critical`.
  Nunca modifica o agente auditado (verificado por teste).
- **Authentication Layer** (`app/auth.py`, fase 2 passo 1) — API keys
  `afk_<public_id>_<secret>`, armazenadas só como hash HMAC-SHA256,
  mostradas uma única vez, revogáveis. A CLI resolve o ator a partir
  da chave validada — nunca de um `--actor` fornecido pelo cliente
  (um `--actor` divergente é rejeitado como falsificação). Admin só
  o Lucas, atribuído no servidor a partir da chave.

- **Isolated Executor** (`app/executor.py`, fase 2 passo 2) —
  supervisor + worker isolado (AppContainer/LPAC + token restrito +
  Job Object, via ctypes). **Preparado, NÃO habilitado**: em
  qualquer host sem AppContainer a execução é recusada
  (`IsolationRefused`, fail-closed); não há comando de CLI para
  execução arbitrária. O único caminho sem isolamento é o modo
  `unsafe_dev`, explicitamente nomeado, desligado por padrão,
  exigindo `unsafe_dev=True` E `AI_FACTORY_UNSAFE_DEV=1`, com
  warning + auditoria em cada uso — só para testar a lógica do
  supervisor no Linux. Nunca é um sandbox.
- **Windows diagnostic** (`tools/diagnostico-windows-readonly.ps1`,
  fase 2 passo 2, já enviado na LTP-030) — script PowerShell
  **somente leitura** que verifica o que a máquina Windows
  realmente suporta (AppContainer, LPAC, token restrito, Job
  Objects, virtualização, Windows Sandbox, edição, admin). Não
  instala, habilita, desabilita ou modifica nada — e não cria
  perfil AppContainer (isso precisa de autorização separada).
  Ver `docs/windows-diagnostic.md`.

## Limites de contenção (impostos em código, com testes)

| Controle | Limite |
|---|---|
| `MAX_DEPTH` | 1 — no MVP, **nenhum agente cria outro agente**; pai tem que ser humano/Factory (mais restritivo que o teto) |
| `MAX_ACTIVE_PER_USER` | 3 agentes ativos por usuário |
| `MAX_CREATIONS_PER_REQUEST` | 1 criação por solicitação |
| `MAX_CONCURRENT_EXECUTIONS` | 2 execuções simultâneas |
| `MAX_EXECUTION_MINUTES` | 5 min por execução |

Hierarquia: Lucas = admin e autoridade final; Orquestrador coordena;
agentes especializados executam só tarefas autorizadas. Nenhum agente
pode ter permissão de modificar as próprias permissões — a Factory
rejeita `admin`, `self_modify`, `self_modify_permissions`, `create_agent`
(e ferramentas fora da allowlist).

## Créditos (unidades internas — não é dinheiro)

- Lucas: ilimitado, **cada bypass é registrado no audit log**.
- Usuários gratuitos: 100 créditos iniciais.
- Planos pagos: cotas em `config.PAID_PLANS` apenas como placeholder.
- Custos: criação = 10, execução = 1/min, auditoria = 1.
- Cada agente tem seu próprio `max_credits`.

## O que o MVP explicitamente NÃO faz

- Sem recursividade (profundidade 1; agente não cria agente).
- Sem pagamentos reais, sem gateway, sem cobrança.
- Sem microsserviços (backend único modular).
- Sem geração de agentes por LLM (só templates).
- Sem execução real de ferramentas — a execução é simulada e limitada.
- Sem deploy, sem tocar NEXUS / JARVIS / nenhum outro projeto.
  Repo público: https://github.com/LucaswBohrer/ai-factory

## Como rodar

```bash
cd ~/workspace/ai-factory-mvp
python3 -m unittest discover -s tests -v   # testes (159: 148 verdes + 11 só-Windows skipped)

# Autenticação (fase 2, passo 1): bootstrap cria a primeira chave
# (lucas, admin) — guarde-a, ela só é mostrada uma vez.
python3 -m app.cli bootstrap
export AI_FACTORY_API_KEY='<chave-do-lucas>'   # ou --api-key em cada comando
python3 -m app.cli create-key --owner alice    # só admin; mostra a chave 1x
python3 -m app.cli list-keys
python3 -m app.cli revoke-key --id <public-id> # só admin

# CLI (DB padrão: ./data.db; override com AI_FACTORY_DB ou --db)
python3 -m app.cli create-agent --owner alice --name helper \
    --kind summarizer --approve
python3 -m app.cli list-agents [--owner alice]
python3 -m app.cli audit-agent --id <agent-id>
python3 -m app.cli credits [--owner alice]
python3 -m app.cli kill-switch on|off
python3 -m app.cli list-audit-log [--limit 20]
python3 -m app.cli execute --id <agent-id> --minutes 2

# Auditoria global (fase 3): determinística, só leitura, só admin (lucas),
# gratuita (sem créditos). 38 regras, relatório PASS/WARN/FAIL/UNVERIFIED.
python3 -m app.cli audit-global [--format json|text]
python3 -m app.cli audit-report --id <report-id>
```

## Layout

```
app/
  config.py         limites, custos, allowlists, templates
  store.py          SQLite (users, agents, executions, audit_log, meta, api_keys, audit_reports)
  resource_core.py  créditos + custo real de API (separado)
  control.py        registro, kill switch, audit log, execução
  factory.py        criação por template + validação + limites
  agent_auditor.py  Agent Auditor (somente leitura; era app/auditor.py, movido na fase 3)
  auditor/          Auditoria global (fase 3): service.py, models.py, rules/ (38 regras)
  auth.py           API keys (HMAC, só hash armazenado, revogação)
  executor.py       supervisor + worker isolado (fase 2, passo 2)
  cli.py            CLI (ator resolvido da chave validada; audit-global passa a chave crua p/ o serviço)
tests/test_mvp.py   31 testes (unittest, stdlib)
tests/test_isolation_ltp022.py   10 testes de isolamento (LTP-022)
tests/test_auth_phase2.py        14 testes de autenticação (fase 2, passo 1)
tests/test_executor_phase2.py    33 testes do executor (fase 2, passo 2; 11 só-Windows)
tests/test_auditor_phase3.py     71 testes do Auditor global (fase 3: 66 + 5 de identidade dos findings)
tools/diagnostico-windows-readonly.ps1  diagnóstico read-only do Windows (fase 2, passo 2)
docs/windows-isolation-matrix.md  matriz de isolamento Windows (fase 2)
docs/windows-diagnostic.md        como rodar o diagnóstico e ler o resultado
docs/auditor-architecture.md     arquitetura detalhada do Auditor global (fase 3)
```

## Testes

`python3 -m unittest discover -s tests -v` — 159 testes em 2026-11-18
(148 verdes + 11 skipped): 88 do baseline (31 MVP + 10 isolamento +
14 autenticação + 33 executor) + 71 do Auditor global (fase 3: 66 +
5 de identidade dos findings, LTP-042 → LTP-043). Os 11
skipped são os testes adversariais só-Windows (LTP-029 §5) — eles
pulam no Linux com "requires Windows AppContainer — not validated" e
SÓ passam no Windows real. Um skip não é um pass. Cobrem: profundidade,
teto de 3 ativos/usuário, dedução de créditos + bypass do Lucas
logado, kill switch bloqueando criação/execução, auditor sinalizando
permissão arriscada, audit log append-only (trigger), Factory
rejeitando self-modify, aprovação humana obrigatória, limites de
execução (5 min, 2 simultâneas), autenticação (chave mostrada 1x e
nunca armazenada em claro, só hash, revogação imediata, ator forjado
rejeitado, operações só de admin), e executor (fail-closed sem
AppContainer, modo unsafe_dev com dupla trava + warning + auditoria,
timeout/kill, teto de saída, env sem segredos, reembolso em recusa,
sem comando de CLI para execução arbitrária).

## Não validado / fora de escopo

- Sandbox real (executor isolado) — passo 2 da fase 2, PREPARADO mas
  não habilitado nem validado. O código (`app/executor.py`) foi
  escrito nesta VM Linux e nunca rodou no Windows; os 10 testes
  adversariais pulam aqui. A matriz em
  `docs/windows-isolation-matrix.md` conclui: subprocesso sozinho
  não é sandbox; a combinação a demonstrar é AppContainer +
  restricted token + Job Object. Execução de código arbitrário
  continua DESABILITADA até os testes adversariais passarem na
  máquina Windows real. Diagnóstico read-only:
  `tools/diagnostico-windows-readonly.ps1` (ver
  `docs/windows-diagnostic.md`).
- Contagem real de tokens por provedor — o campo `real_api_cost` existe
  e é registrado, mas nenhum provedor real está ligado.
- Multi-tenant de verdade, antifraude de créditos (farming de contas)
  — fora do escopo da fase 2, passo 1. O segredo do servidor vive na
  tabela `meta` do próprio banco (limitação documentada); chaves não
  expiram — revogue-as.
- Validação no Windows — diagnóstico read-only rodado na máquina real
  do Lucas em 2026-11-18 (Windows 11 Pro, build 26300; ver
  `docs/windows-diagnostic-result-2026-11-18.md`); o probe funcional do
  AppContainer foi recusado por ele — é o computador do trabalho, o
  único que ele tem — e a recusa é definitiva para essa máquina. O MVP
  só foi testado nesta VM (testes + smoke test de CLI).
