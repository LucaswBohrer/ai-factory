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
- **Agent Auditor** (`app/auditor.py`) — primeiro agente, **somente leitura**.
  Analisa config, permissões, riscos, ciclos de execução e consumo de um
  agente e devolve achados com severidade `info` / `warning` / `critical`.
  Nunca modifica o agente auditado (verificado por teste).

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
- Sem deploy, sem repo, sem tocar NEXUS / JARVIS / nenhum outro projeto.

## Como rodar

```bash
cd ~/workspace/ai-factory-mvp
python3 -m unittest discover -s tests -v   # testes

# CLI (DB padrão: ./data.db; override com AI_FACTORY_DB ou --db)
python3 -m app.cli create-agent --owner alice --name helper \
    --kind summarizer --approve
python3 -m app.cli list-agents [--owner alice]
python3 -m app.cli audit-agent --id <agent-id> --actor lucas
python3 -m app.cli credits [--owner alice]
python3 -m app.cli kill-switch on|off --actor lucas
python3 -m app.cli list-audit-log [--limit 20]
python3 -m app.cli execute --id <agent-id> --actor alice --minutes 2
```

## Layout

```
app/
  config.py         limites, custos, allowlists, templates
  store.py          SQLite (users, agents, executions, audit_log, meta)
  resource_core.py  créditos + custo real de API (separado)
  control.py        registro, kill switch, audit log, execução
  factory.py        criação por template + validação + limites
  auditor.py        Agent Auditor (somente leitura)
  cli.py            CLI
tests/test_mvp.py   31 testes (unittest, stdlib)
```

## Testes

`python3 -m unittest discover -s tests -v` — 31 testes, todos verdes
em 2026-11-18 (ver relatório de entrega). Cobrem: profundidade,
teto de 3 ativos/usuário, dedução de créditos + bypass do Lucas
logado, kill switch bloqueando criação/execução, auditor sinalizando
permissão arriscada, audit log append-only (trigger), Factory
rejeitando self-modify, aprovação humana obrigatória, e limites de
execução (5 min, 2 simultâneas).

## Não validado / fora de escopo

- Sandbox real (Docker/subprocesso isolado) — no MVP, "isolamento" é
  lógico (permissões mínimas + limites), não um sandbox de SO.
- Contagem real de tokens por provedor — o campo `real_api_cost` existe
  e é registrado, mas nenhum provedor real está ligado.
- Multi-tenant de verdade, antifraude de créditos (farming de contas),
  autenticação de usuários — fora do MVP.
- Validação no Windows / por Lucas — pendente; este MVP só foi testado
  nesta VM (testes + smoke test de CLI).
