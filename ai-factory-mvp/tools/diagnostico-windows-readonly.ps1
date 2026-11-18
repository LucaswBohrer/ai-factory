# Diagnóstico read-only do Windows — AI Factory, Fase 2 passo 2 (LTP-029)
# ---------------------------------------------------------------------------
# O QUE FAZ: só LÊ informações do sistema e imprime na tela. NÃO instala,
# NÃO habilita, NÃO desabilita, NÃO cria perfil AppContainer, NÃO altera
# registro, firewall, recursos do Windows ou qualquer arquivo.
# NÃO executa código arbitrário e NÃO testa isolamento (isso é o passo 2,
# com probe separado e autorização própria).
#
# COMO RODAR: duplo clique no arquivo, OU no PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\diagnostico-windows-readonly.ps1
# Copiar TODA a saída e colar no chat do Theo.
#
# O QUE COLETA (tabela pedida na LTP-029 §3):
#   Versão do Windows, Edição, AppContainer, LPAC, Token restrito,
#   Job Objects, Virtualização, Windows Sandbox, Permissões administrativas.
#   + Python (ctypes é stdlib) e RAM/CPU, que decidem se o Sandbox é viável.
#
# Compatível com Windows PowerShell 5.1. Sem módulos externos.

$ErrorActionPreference = 'Continue'

function Show-Item($nome, $valor, $status) {
    Write-Output ("{0,-32} : {1} [{2}]" -f $nome, $valor, $status)
}

function Get-FeatureState($nome) {
    # Consulta SOMENTE o estado do recurso opcional. Não habilita nada.
    try {
        $f = Get-WindowsOptionalFeature -Online -FeatureName $nome -ErrorAction Stop
        if ($f.State -eq 'Enabled') { return 'Habilitado' }
        return "Desabilitado (estado atual: $($f.State))"
    } catch {
        return 'Não verificado — a consulta falhou (talvez precise de admin)'
    }
}

Write-Output '=== AI Factory — Diagnóstico read-only do Windows (LTP-029) ==='
Write-Output ("Data/hora: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output ''

# --- Sistema operacional (leitura via CIM, sem alteração) ---
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    Show-Item 'Versão do Windows' ("{0} (build {1})" -f $os.Caption, $os.BuildNumber) 'Disponível'
    Show-Item 'Edição' $os.Caption 'Disponível'
    Show-Item 'Arquitetura' $os.OSArchitecture 'Disponível'
    $ramGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    Show-Item 'RAM total' ("{0} GB" -f $ramGB) 'Disponível'
} catch {
    Show-Item 'Versão do Windows' 'falha na leitura' 'Não verificado'
}

try {
    $cpu = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
    Show-Item 'Processador' $cpu.Name 'Disponível'
    Show-Item 'Núcleos' $cpu.NumberOfCores 'Disponível'
    $virt = if ($cpu.VirtualizationFirmwareEnabled) { 'Habilitada no firmware' } else { 'Desabilitada ou não detectada no firmware' }
    Show-Item 'Virtualização (firmware)' $virt 'Disponível'
} catch {
    Show-Item 'Virtualização (firmware)' 'falha na leitura' 'Não verificado'
}

Write-Output ''

# --- Permissões administrativas (só verifica o token atual) ---
try {
    $ident = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($ident)
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Show-Item 'Sessão com admin' ($(if ($isAdmin) { 'Sim' } else { 'Não' })) 'Disponível'
} catch {
    Show-Item 'Sessão com admin' 'falha na leitura' 'Não verificado'
}

Write-Output ''

# --- Windows Sandbox e Hyper-V (consulta de estado, sem habilitar) ---
Show-Item 'Windows Sandbox' (Get-FeatureState 'Containers-DisposableClientVM') 'Disponível'
Show-Item 'Hyper-V' (Get-FeatureState 'Microsoft-Hyper-V') 'Disponível'
Show-Item 'Virtual Machine Platform' (Get-FeatureState 'VirtualMachinePlatform') 'Disponível'

Write-Output ''

# --- Mecanismos de isolamento: disponibilidade da API por build ---
# Job Objects existem desde o XP; token restrito desde o XP/2003;
# AppContainer/LPAC desde o Windows 8. A presença da API NÃO prova que o
# isolamento funciona na prática — a prova funcional é o probe do passo 2.
# Aqui só registramos que a API existe nesta build do Windows.
try {
    $build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
    $apiStatus = if ($build -ge 9200) { 'Disponível (API presente nesta build)' } else { 'Não disponível (build anterior ao Windows 8)' }
    Show-Item 'Job Objects (API)' $apiStatus 'Disponível'
    Show-Item 'Token restrito (API)' $apiStatus 'Disponível'
    Show-Item 'AppContainer (API)' $apiStatus 'Disponível'
    Show-Item 'LPAC (API)' $apiStatus 'Disponível'
    Write-Output '  Obs.: API presente != isolamento comprovado. A comprovação'
    Write-Output '  funcional (ler fora do diretório, rede bloqueada, limites,'
    Write-Output '  kill por timeout) é o probe do passo 2, ainda NÃO executado.'
} catch {
    Show-Item 'Job Objects / Token / AppContainer' 'falha na leitura da build' 'Não verificado'
}

Write-Output ''

# --- Python (o executor usaria ctypes, que é stdlib) ---
try {
    $py = (python --version 2>&1).ToString().Trim()
    if ($py) { Show-Item 'Python' $py 'Disponível' } else { Show-Item 'Python' 'não encontrado no PATH' 'Não disponível' }
} catch {
    Show-Item 'Python' 'não encontrado no PATH' 'Não disponível'
}

Write-Output ''
Write-Output '=== Fim do diagnóstico — nada foi instalado, habilitado ou alterado ==='
Write-Output ''
Read-Host 'Pressione ENTER para fechar esta janela'
