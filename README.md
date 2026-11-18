# ai-factory

A IA que cria outras IAs para gerenciar IAs que geram IAs — versão
controlada (MVP).

Criado em 2026-11-18 por Lucas Welter Bohrer, com Theo (Muse) executando
e ChatGPT arquitetando, pelo Lucas Team Protocol (LTP).

## O que tem aqui

- `ai-factory-mvp/` — o MVP aprovado: backend único modular em Python
  stdlib, com Factory (criação por template), Control (registro,
  permissões, audit log append-only, kill switch), Resource Core
  (créditos e limites) e Agent Auditor (somente leitura).
  Limites: profundidade 1 (sem recursividade no MVP), 3 agentes ativos
  por usuário, 1 criação por solicitação, 2 execuções simultâneas,
  5 min por execução. Lucas é admin, isento de créditos, com bypass
  logado. Sem pagamentos reais no MVP. Leia
  `ai-factory-mvp/README.md` e `ai-factory-mvp/docs/`.
- `ltp/` — o canal Theo ↔ ChatGPT: `LTP.md` (protocolo v1.3) e
  `mailbox.md` (todas as mensagens LTP-001 em diante). O Lucas é o
  carteiro entre os dois chats; este repo é o arquivo compartilhado
  dos dois agentes.

## Status

MVP build 1 pronto, 31/31 testes verdes (ver
`ai-factory-mvp/docs/evidence-containment.md`). Revisão técnica do
ChatGPT pendente (LTP-021). Gaps de isolamento conhecidos, documentados
em `ai-factory-mvp/docs/known-vulnerabilities.md` — corrigir antes de
qualquer uso multi-usuário.

## Como rodar o MVP

```bash
cd ai-factory-mvp
python3 -m unittest discover -s tests -v
python3 -m app.cli create-agent --owner alice --name helper \
    --kind summarizer --approve
```
