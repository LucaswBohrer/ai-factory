# Resultado do diagnóstico read-only — máquina do Lucas (2026-11-18 15:21)

Saída integral colada pelo Lucas no chat, após rodar
`ai-factory-mvp/tools/diagnostico-windows-readonly.ps1` com duplo clique
(Executar com o PowerShell). Nada foi instalado, habilitado ou alterado.

```
=== AI Factory — Diagnóstico read-only do Windows (LTP-029) ===
Data/hora: 2026-11-18 15:21:25

Versão do Windows               : Microsoft Windows 11 Pro (build 26300) [Disponível]
Edição                         : Microsoft Windows 11 Pro [Disponível]
Arquitetura                      : 64 bits [Disponível]
RAM total                        : 15,7 GB [Disponível]
Processador                      : 13th Gen Intel(R) Core(TM) i7-1355U [Disponível]
Núcleos                         : 10 [Disponível]
Virtualização (firmware)       : Desabilitada ou não detectada no firmware [Disponível]

Sessão com admin                : Não [Disponível]

Windows Sandbox                  : Não verificado — a consulta falhou (talvez precise de admin) [Disponível]
Hyper-V                          : Não verificado — a consulta falhou (talvez precise de admin) [Disponível]
Virtual Machine Platform         : Não verificado — a consulta falhou (talvez precise de admin) [Disponível]

Job Objects (API)                : Disponível (API presente nesta build) [Disponível]
Token restrito (API)             : Disponível (API presente nesta build) [Disponível]
AppContainer (API)               : Disponível (API presente nesta build) [Disponível]
LPAC (API)                       : Disponível (API presente nesta build) [Disponível]
  Obs.: API presente != isolamento comprovado. A comprovação
  funcional (ler fora do diretório, rede bloqueada, limites,
  kill por timeout) é o probe do passo 2, ainda NÃO executado.

Python                           : Python 3.14.7 [Disponível]

=== Fim do diagnóstico — nada foi instalado, habilitado ou alterado ===
```

## Interpretação

- **Favorável:** Windows 11 Pro 64-bit, build 26300 — AppContainer/LPAC,
  token restrito e Job Objects têm API presente nesta build. Python 3.14.7.
  RAM 15,7 GB, 10 núcleos — folga para os limites do executor.
- **Limitações:** sessão **sem admin** — as consultas de Windows Sandbox,
  Hyper-V e Virtual Machine Platform falharam (provavelmente precisam de
  admin) e saem como **Não verificado**, conforme a regra da LTP-031.
  Virtualização no firmware aparece como **desabilitada ou não detectada** —
  sem ela, o Windows Sandbox não é viável sem mudança de BIOS + admin.
- **Conclusão:** o caminho AppContainer/LPAC + token restrito + Job Object
  continua viável *em princípio* (API presente), mas **API presente não é
  isolamento comprovado**. Só o probe funcional do passo 2 (10 testes
  adversariais) demonstra o isolamento — e ele precisa de autorização
  separada do Lucas (cria um perfil AppContainer temporário, objeto
  persistente no sistema).
