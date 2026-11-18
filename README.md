# ai-factory

A IA que cria outras IAs para gerenciar IAs que geram IAs — versão
controlada. Um backend único e modular em **Python stdlib** (sem
dependências externas, sem chamadas de rede, sem LLM no núcleo),
construído em fases, cada uma revisada antes da próxima começar.

Criado em 2026-11-18 por Lucas Welter Bohrer, com Theo (Muse)
executando e ChatGPT arquitetando, pelo
[Lucas Team Protocol (LTP)](ltp/LTP.md).

> **Atenção:** este é um projeto em desenvolvimento controlado, não um
> produto pronto. Execução de código arbitrário está **desabilitada**,
> não há deploy público, pagamentos reais ou recursividade. Leia
> [Limitações](#limitações-honestas) antes de usar.

## Status — 2026-11-18

| Fase | Estado | Resultado |
|---|---|---|
| 1 — MVP controlado | ✅ Encerrada (LTP-024) | 41 testes; aprovação condicional |
| 2 — Auth + executor isolado | ✅ Encerrada (LTP-034) | API keys HMAC + executor preparado, **sem isolamento validado** |
| 3 — Auditor trabalhando | ✅ Encerrada (LTP-044) | 38 regras determinísticas, build aprovado |
| 4 | ⏸️ Não iniciada | Proposta pendente |

**Testes:** `159` no total — **148 passando, 11 pulados**.
Os 11 pulados são os testes adversariais só-Windows do executor
(LTP-029 §5): eles pulam no Linux com
"requires Windows AppContainer — not validated" e só passam no
Windows real. **Um skip não é um pass.**

## O que o sistema faz

- **Factory** — cria agentes a partir de templates (`auditor`,
  `summarizer`, `researcher`). Sem geração por LLM. Toda criação
  exige aprovação humana explícita.
- **Control** — registro, suspensão/kill de agentes, log de auditoria
  append-only (triggers no SQLite bloqueiam UPDATE e DELETE) e kill
  switch (só o Lucas liga/desliga).
- **Resource Core** — créditos internos (Lucas ilimitado com bypass
  logado; usuários gratuitos começam com 100).
- **Authentication Layer** — API keys `afk_...`, armazenadas só como
  hash HMAC-SHA256, mostradas uma única vez, revogáveis. O ator é
  resolvido a partir da chave validada — nunca de um `--actor`
  fornecido pelo cliente.
- **Isolated Executor** — supervisor + worker isolado (AppContainer /
  LPAC + token restrito + Job Object). **Preparado, NÃO habilitado:**
  sem AppContainer, a execução é recusada (fail-closed). Não há
  comando de CLI para execução arbitrária.
- **Auditor global** — 38 verificações determinísticas sobre
  permissões, créditos, integridade do log, chaves e eventos de
  segurança. Só leitura, só o Lucas, gratuito. Devolve um relatório
  PASS / WARN / FAIL / UNVERIFIED com evidências.
  **Um relatório todo PASS não significa sistema seguro** — significa
  apenas que as verificações implementadas não encontraram violações
  no escopo examinado.

## Limites de contenção (impostos em código, com testes)

| Controle | Limite |
|---|---|
| Profundidade | 1 — **nenhum agente cria outro agente** |
| Agentes ativos por usuário | 3 |
| Criações por solicitação | 1 |
| Execuções simultâneas | 2 |
| Duração por execução | 5 min |

Hierarquia: Lucas = admin e autoridade final. Nenhum agente pode
modificar as próprias permissões — a Factory rejeita `admin`,
`self_modify`, `self_modify_permissions` e `create_agent`.

## Como rodar

```bash
git clone https://github.com/LucaswBohrer/ai-factory.git
cd ai-factory/ai-factory-mvp

python3 -m unittest discover -s tests   # 159 testes

# 1. Bootstrap: cria a primeira chave (lucas, admin).
#    Guarde-a — ela só é mostrada uma vez.
python3 -m app.cli bootstrap
export AI_FACTORY_API_KEY='<chave-do-lucas>'

# 2. Criar um agente (só admin cria chaves; criação exige --approve)
python3 -m app.cli create-key --owner alice
python3 -m app.cli create-agent --owner alice --name helper \
    --kind summarizer --approve

# 3. Auditoria global (só lucas, gratuita, 38 regras)
python3 -m app.cli audit-global
python3 -m app.cli audit-report --id <report-id>
```

Outros comandos: `list-agents`, `audit-agent --id`, `credits`,
`kill-switch on|off`, `list-audit-log`, `list-keys`, `revoke-key`.
O banco padrão é `./data.db` (override com `AI_FACTORY_DB` ou
`--db`). Detalhes em [`ai-factory-mvp/README.md`](ai-factory-mvp/README.md).

## Estrutura

```
ai-factory-mvp/
  app/
    config.py        limites, custos, allowlists, templates
    store.py         SQLite (users, agents, executions, audit_log, api_keys, audit_reports)
    factory.py       criação por template + validação + limites
    control.py       registro, kill switch, audit log, execução
    resource_core.py créditos + custo real de API (separado)
    auth.py          API keys (HMAC, só hash armazenado, revogação)
    executor.py      supervisor + worker isolado (preparado, não habilitado)
    agent_auditor.py Agent Auditor (somente leitura)
    auditor/         Auditoria global: service.py, models.py, rules/ (38 regras)
    cli.py           CLI (ator resolvido da chave validada)
  tests/             159 testes (unittest, stdlib)
  tools/diagnostico-windows-readonly.ps1   diagnóstico read-only do Windows
  docs/              arquitetura, permissões, contenção, vulnerabilidades,
                     matriz de isolamento, arquitetura do Auditor,
                     relatórios de encerramento das fases 1–3
ltp/
  LTP.md             o protocolo v1.3 (Theo ↔ ChatGPT, Lucas é o carteiro)
  mailbox.md         todas as mensagens LTP-001 em diante
```

## Documentação

- [`ai-factory-mvp/docs/architecture.md`](ai-factory-mvp/docs/architecture.md) — arquitetura do sistema
- [`ai-factory-mvp/docs/auditor-architecture.md`](ai-factory-mvp/docs/auditor-architecture.md) — arquitetura detalhada do Auditor (rev 2)
- [`ai-factory-mvp/docs/status.md`](ai-factory-mvp/docs/status.md) — status do projeto e encerramento da fase 1
- [`ai-factory-mvp/docs/phase-2-closure.md`](ai-factory-mvp/docs/phase-2-closure.md) — encerramento da fase 2
- [`ai-factory-mvp/docs/phase-3-closure.md`](ai-factory-mvp/docs/phase-3-closure.md) — encerramento da fase 3
- [`ai-factory-mvp/docs/known-vulnerabilities.md`](ai-factory-mvp/docs/known-vulnerabilities.md) — vulnerabilidades conhecidas, com status
- [`ai-factory-mvp/docs/windows-isolation-matrix.md`](ai-factory-mvp/docs/windows-isolation-matrix.md) — por que subprocesso não é sandbox
- [`ltp/mailbox.md`](ltp/mailbox.md) — o canal Theo ↔ ChatGPT, mensagem por mensagem

## Limitações honestas

- **Sem sandbox validado.** O executor foi escrito numa VM Linux e
  nunca rodou no Windows; os 10 testes adversariais pulam aqui.
  Execução arbitrária continua desabilitada até passarem numa
  máquina Windows real — e o probe não pode rodar no computador de
  trabalho do Lucas (decisão dele, definitiva para essa máquina).
- **Sem livro de créditos.** Cobranças existem só como texto livre;
  reconciliação exata exige uma tabela `ledger` (proposta, não
  construída). Por isso 7 das 38 regras do Auditor devolvem
  UNVERIFIED.
- **Log append-only não é à prova de adulteração.** Os triggers não
  impedem INSERTs fabricados nem substituição do banco inteiro.
- **Tentativas negadas são invisíveis por desenho.** O código rejeita
  antes de registrar, então o log contém só eventos permitidos.
- **Sem contagem real de tokens por provedor**, sem antifraude, sem
  pagamentos, sem deploy, sem multi-tenant de verdade.
- **Segredo do servidor vive na tabela `meta` do próprio banco**
  (limitação local documentada); chaves não expiram — revogue-as.

## Regras do projeto

- Nenhuma mudança de contenção sem nova aprovação do Lucas.
- Nada de recursividade, pagamentos reais ou deploy sem decisão
  explícita dele.
- Cada fase só começa depois da anterior ser revisada e encerrada.
- O Lucas é usuário final, não tester: criação e testes ficam com
  Theo + ChatGPT; ele só recebe a entrega consolidada no final.
