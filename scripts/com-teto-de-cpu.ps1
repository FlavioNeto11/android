<#
.SYNOPSIS
  Roda um comando (e tudo o que ele criar) debaixo de um TETO DE CPU do Windows, para o funil não competir com os emuladores
  de conta real (29.174). Não toca o `.wslconfig`, o túnel, o relógio nem processo nenhum que não seja descendente do comando.

.DESCRIPTION
  Cria um Job Object com controle de taxa de CPU em teto rígido (`JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP`: percentual do TOTAL de
  threads do host, por intervalo de escalonamento) e cria o comando SUSPENSO diretamente nele (`CreateProcess` + `AssignProcessToJobObject`
  + `ResumeThread`): nem o comando nem nenhum descendente (pytest, os workers do xdist, qualquer neto) passa um instante fora do teto.
  Não é o `&` do PowerShell que cria o processo: ele não herda o job quando o pwsh já está dentro de outro job, e o teto não valeria.
  Não precisa de administrador e some quando o comando termina. Opcionalmente fixa a árvore nos núcleos de eficiência (E) de uma CPU híbrida, para deixar os núcleos P
  com os emuladores. `KILL_ON_JOB_CLOSE`: se este processo morrer, a árvore morre junto (nada fica órfão consumindo CPU).

  O código de saída é o do comando. Prioridade ociosa continua valendo por cima (o teto não a substitui).

  Uso (o comando NÃO vai solto na linha: `-m`, `-n`, `-c` etc. seriam lidos como parâmetros deste script; por isso há duas formas):
    pwsh -File scripts\com-teto-de-cpu.ps1 -Teto 40 -NucleosE -Linha "python -m pytest -q -n 6 tests\x.py"
    pwsh -File scripts\com-teto-de-cpu.ps1 -Teto 40 -ComandoJson '["python","-m","pytest","-q"]'      # argv exato, sem aspas
    pwsh -File scripts\com-teto-de-cpu.ps1 -Teto 40 -NucleosE -Simular      # só mostra o que faria (e as classes de núcleo)

.PARAMETER Teto
  1 a 100: percentual do total de threads lógicos que a árvore pode usar (teto rígido). 40 num host de 22 threads ≈ 9 threads.
.PARAMETER NucleosE
  Fixa a árvore nos núcleos de eficiência (nem os mais rápidos nem os de baixo consumo); recusa em CPU sem classes distintas.
.PARAMETER Afinidade
  Máscara de afinidade explícita (decimal ou 0x…), no grupo de processadores 0 (até 64 threads). Exclusivo com -NucleosE.
.PARAMETER BatimentoS
  A cada quantos segundos imprimir a CPU que a árvore já usou ("com-teto-de-cpu: 30 s: árvore 14,2 s de CPU ..."); 0 desliga (padrão 60).
  Serve para conferir, ainda no primeiro minuto, que o comando está DENTRO do job: se a árvore marca 0 s depois de `-ZeroAposS` s (padrão
  20), o wrapper avisa (o comando provavelmente escapou, como o `pwsh` 7).
.PARAMETER PermitirPwsh
  O wrapper RECUSA (código 125) comando que usa `pwsh` (PowerShell 7, app MSIX do host): ele escapa do job e o teto não valeria. Esta
  chave ignora a trava (só para teste).
.PARAMETER ArquivoDeTeto
  Arquivo de uma linha com o percentual de teto (1 a 100) que vale AGORA. A cada 2 s o wrapper o lê e, se mudou, troca o teto do job
  que já roda (teto por etapa: quem encadeia as etapas escreve o número novo antes de cada uma). Valor inválido é ignorado com aviso.
.PARAMETER ZeroAposS
  Segundos antes de o batimento acusar árvore com 0 s de CPU (padrão 20).
.PARAMETER Simular
  Não executa nada: imprime um JSON com o teto, a máscara e as classes de eficiência detectadas.
.PARAMETER Linha
  A linha de comando para o `cmd.exe /d /c` (sintaxe e aspas do cmd; `&&` e redirecionamento valem). NÃO é PowerShell: os processos que
  o PowerShell cria escapam do job. É ferramenta local de quem roda o funil; não aceite texto de fonte externa aqui.
.PARAMETER ComandoJson
  O comando como lista JSON de argumentos (argv exato; o primeiro é o executável). Prefira esta forma em automação.
#>
[CmdletBinding()]
param(
  [ValidateRange(1, 100)][int]$Teto = 40,
  [switch]$NucleosE,
  [string]$Afinidade = '',
  [switch]$Simular,
  [string]$Linha = '',
  [string]$ComandoJson = '',
  [ValidateRange(0, 3600)][int]$BatimentoS = 60,
  [ValidateRange(1, 3600)][int]$ZeroAposS = 20,
  [switch]$PermitirPwsh,
  [string]$ArquivoDeTeto = ''
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne 'Win32NT') { throw 'o teto de CPU por Job Object só existe no Windows.' }
if ($NucleosE -and $Afinidade) { throw '-NucleosE e -Afinidade não se combinam: escolha um.' }

if (-not ('TetoDeCpu' -as [type])) {
  Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class TetoDeCpu {
  [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  static extern IntPtr CreateJobObject(IntPtr atributos, string nome);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool SetInformationJobObject(IntPtr job, int classe, IntPtr info, uint tamanho);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool AssignProcessToJobObject(IntPtr job, IntPtr processo);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct STARTUPINFO {
    public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
  }
  [StructLayout(LayoutKind.Sequential)]
  struct PROCESS_INFORMATION { public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId; }
  [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  static extern bool CreateProcessW(string app, System.Text.StringBuilder linha, IntPtr pa, IntPtr ta, bool herdar, uint flags,
                                    IntPtr env, string pasta, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
  [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
  [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr h, uint ms);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetExitCodeProcess(IntPtr h, out uint codigo);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr h, uint codigo);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int id);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool GetSystemCpuSetInformation(IntPtr info, uint tamanho, out uint devolvido, IntPtr processo, uint flags);

  const int InfoEstendida = 9;        // JobObjectExtendedLimitInformation
  const int InfoTaxaDeCpu = 15;       // JobObjectCpuRateControlInformation
  const uint LimiteAfinidade = 0x10;
  const uint MatarAoFechar = 0x2000;
  const uint TaxaLigada = 0x1;
  const uint TaxaTeto = 0x4;          // HARD_CAP

  // Classe de eficiência de cada thread lógico (índice no grupo 0 -> classe). Maior classe = mais rápido.
  public static Dictionary<int, int> Classes() {
    var r = new Dictionary<int, int>();
    uint n;
    GetSystemCpuSetInformation(IntPtr.Zero, 0, out n, IntPtr.Zero, 0);
    if (n == 0) return r;
    IntPtr buf = Marshal.AllocHGlobal((int)n);
    try {
      if (!GetSystemCpuSetInformation(buf, n, out n, IntPtr.Zero, 0)) return r;
      int pos = 0;
      while (pos < n) {
        int tam = Marshal.ReadInt32(buf, pos);
        if (tam <= 0) break;
        if (Marshal.ReadInt32(buf, pos + 4) == 0) {                       // CpuSetInformation
          if (Marshal.ReadInt16(buf, pos + 12) == 0) {                    // grupo 0
            r[Marshal.ReadByte(buf, pos + 14)] = Marshal.ReadByte(buf, pos + 18);
          }
        }
        pos += tam;
      }
    } finally { Marshal.FreeHGlobal(buf); }
    return r;
  }

  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool QueryInformationJobObject(IntPtr job, int classe, IntPtr info, uint tamanho, out uint devolvido);

  // Segundos de CPU (usuário + kernel) que TODA a árvore do job gastou até agora (JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).
  public static double CpuDoJob(IntPtr job) {
    IntPtr b = Marshal.AllocHGlobal(48);
    try {
      uint r;
      if (!QueryInformationJobObject(job, 1, b, 48, out r)) return -1;
      return (Marshal.ReadInt64(b, 0) + Marshal.ReadInt64(b, 8)) / 1e7;   // unidades de 100 ns
    } finally { Marshal.FreeHGlobal(b); }
  }

  // Cria o job (teto de CPU, afinidade opcional, matar ao fechar). Quem roda o comando o põe lá dentro (Rodar).
  public static IntPtr Criar(int tetoPercentual, ulong mascara) {
    IntPtr job = CreateJobObject(IntPtr.Zero, null);
    if (job == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject");
    IntPtr ext = Marshal.AllocHGlobal(144);                                // JOBOBJECT_EXTENDED_LIMIT_INFORMATION (x64)
    try {
      for (int i = 0; i < 144; i++) Marshal.WriteByte(ext, i, 0);
      uint flags = MatarAoFechar | (mascara != 0 ? LimiteAfinidade : 0);
      Marshal.WriteInt32(ext, 16, (int)flags);                            // BasicLimitInformation.LimitFlags
      Marshal.WriteInt64(ext, 48, (long)mascara);                         // BasicLimitInformation.Affinity
      if (!SetInformationJobObject(job, InfoEstendida, ext, 144))
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject(limites)");
    } finally { Marshal.FreeHGlobal(ext); }
    IntPtr taxa = Marshal.AllocHGlobal(8);                                // JOBOBJECT_CPU_RATE_CONTROL_INFORMATION
    try {
      Marshal.WriteInt32(taxa, 0, (int)(TaxaLigada | TaxaTeto));
      Marshal.WriteInt32(taxa, 4, tetoPercentual * 100);                  // 1/100 de ponto percentual: 10000 = 100 %
      if (!SetInformationJobObject(job, InfoTaxaDeCpu, taxa, 8))
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject(taxa de CPU)");
    } finally { Marshal.FreeHGlobal(taxa); }
    return job;
  }

  // Troca o teto de um job que já roda (a árvore em andamento passa a valer o novo percentual na hora).
  public static void AjustarTeto(IntPtr job, int tetoPercentual) {
    IntPtr taxa = Marshal.AllocHGlobal(8);
    try {
      Marshal.WriteInt32(taxa, 0, (int)(TaxaLigada | TaxaTeto));
      Marshal.WriteInt32(taxa, 4, tetoPercentual * 100);
      if (!SetInformationJobObject(job, InfoTaxaDeCpu, taxa, 8))
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject(ajuste da taxa)");
    } finally { Marshal.FreeHGlobal(taxa); }
  }

  // Roda `linha` (já com aspas de CreateProcess) debaixo do job: o processo nasce SUSPENSO, entra no job e só então roda, de modo que
  // nem ele nem nenhum descendente passa um instante fora do teto. Devolve o código de saída do comando; espera o processo acabar.
  public static int Rodar(IntPtr job, string linha, int cadaMs, Action aoPassar) {
    var si = new STARTUPINFO();
    si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
    si.dwFlags = 0x100;                                                    // STARTF_USESTDHANDLES: a saída vai para onde a nossa vai
    si.hStdInput = GetStdHandle(-10); si.hStdOutput = GetStdHandle(-11); si.hStdError = GetStdHandle(-12);
    PROCESS_INFORMATION pi;
    var cmd = new System.Text.StringBuilder(linha);
    if (!CreateProcessW(null, cmd, IntPtr.Zero, IntPtr.Zero, true, 0x4 | 0x400, IntPtr.Zero, null, ref si, out pi))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "CreateProcess");
    try {
      if (!AssignProcessToJobObject(job, pi.hProcess)) {
        int erro = Marshal.GetLastWin32Error();
        TerminateProcess(pi.hProcess, 1);                                  // nunca roda fora do teto
        throw new System.ComponentModel.Win32Exception(erro, "AssignProcessToJobObject");
      }
      if (ResumeThread(pi.hThread) == 0xFFFFFFFF) {
        int erro = Marshal.GetLastWin32Error();
        TerminateProcess(pi.hProcess, 1);
        throw new System.ComponentModel.Win32Exception(erro, "ResumeThread");
      }
      if (cadaMs <= 0 || aoPassar == null) WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
      else while (WaitForSingleObject(pi.hProcess, (uint)cadaMs) == 0x102) aoPassar();   // 0x102 = WAIT_TIMEOUT: o comando segue rodando
      uint codigo;
      if (!GetExitCodeProcess(pi.hProcess, out codigo)) return 1;
      return unchecked((int)codigo);
    } finally { CloseHandle(pi.hThread); CloseHandle(pi.hProcess); }
  }
}
'@
}

# --- núcleos de eficiência e máscara ----------------------------------------------------------------------------------
$classes = [TetoDeCpu]::Classes()                                  # índice lógico -> classe de eficiência
$distintas = @($classes.Values | Sort-Object -Unique)
[uint64]$mascara = 0
$descricaoDaAfinidade = 'nenhuma'
if ($Afinidade) {
  try { $mascara = [Convert]::ToUInt64($Afinidade.Trim(), $(if ($Afinidade.Trim() -match '^0[xX]') { 16 } else { 10 })) }
  catch { throw "-Afinidade '$Afinidade' não é uma máscara numérica válida (decimal ou 0x…)." }
  if ($mascara -eq 0) { throw '-Afinidade 0 não deixaria nenhum processador.' }
  $descricaoDaAfinidade = 'explícita'
} elseif ($NucleosE) {
  if ($distintas.Count -lt 2) { throw 'esta CPU não tem classes de eficiência distintas (não é híbrida): -NucleosE não se aplica; use -Afinidade.' }
  # O Windows pode reportar 3 classes (P, E, baixo consumo; os E são a do meio) ou 2 (os E já vêm junto dos de baixo consumo): nesse
  # caso são a menor. Os índices NÃO são contíguos (num Core Ultra 9 185H os E+LP ficam em 2–9 e 20–21): vem sempre da API.
  $alvo = if ($distintas.Count -ge 3) { $distintas[1] } else { $distintas[0] }
  foreach ($k in $classes.Keys) { if ($classes[$k] -eq $alvo -and $k -lt 64) { $mascara = $mascara -bor ([uint64]1 -shl [int]$k) } }
  if ($mascara -eq 0) { throw 'não achei núcleos de eficiência no grupo 0 de processadores.' }
  $descricaoDaAfinidade = "núcleos E (classe $alvo)"
}
$total = [Environment]::ProcessorCount
$plano = [ordered]@{
  teto_pct = $Teto; threads_totais = $total; teto_em_threads = [math]::Round($total * $Teto / 100, 1)
  afinidade = $descricaoDaAfinidade; mascara = ('0x{0:X}' -f $mascara)
  classes_de_eficiencia = @($distintas); threads_por_classe = @($distintas | ForEach-Object { $c = $_; [pscustomobject]@{ classe = $c; threads = @($classes.Keys | Where-Object { $classes[$_] -eq $c }).Count } })
}
if ($Simular) { $plano | ConvertTo-Json -Depth 4; return }

if ($Linha -and $ComandoJson) { throw '-Linha e -ComandoJson não se combinam: escolha um.' }
$cmd = @()
if ($ComandoJson) {
  try { $cmd = @($ComandoJson | ConvertFrom-Json) } catch { throw '-ComandoJson não é uma lista JSON válida.' }
  if ($cmd.Count -eq 0 -or -not ($cmd[0] -is [string]) -or -not $cmd[0]) { throw '-ComandoJson precisa de uma lista de textos cujo primeiro item é o executável.' }
  $cmd = @($cmd | ForEach-Object { [string]$_ })
}
if (-not $Linha -and $cmd.Count -eq 0) { throw 'faltou o comando: -Linha ''...'' ou -ComandoJson ''["exe","arg"]''.' }

# Linha de comando no formato do CreateProcess: cada argumento entre aspas, com as barras e aspas escapadas (regras do CommandLineToArgvW).
function ConvertTo-ArgumentoWin([string]$a) {
  if ($a -ne '' -and $a -notmatch '[\s"]') { return $a }
  $r = [regex]::Replace($a, '(\\*)"', '$1$1\"')
  $r = [regex]::Replace($r, '(\\+)$', '$1$1')
  return '"' + $r + '"'
}
if ($Linha) {
  # `cmd.exe /d /c`: o cmd cria os filhos com CreateProcess e eles entram no job. Um `pwsh -Command` NÃO serve: os processos nativos que o
  # PowerShell cria escapam do job (medido em 06/10/2026: a árvore inteira ficava fora do teto e usava ~0 % dele).
  $cmd = @($env:ComSpec, '/d', '/c', $Linha)
}
$linhaDeComando = ($cmd | ForEach-Object { ConvertTo-ArgumentoWin $_ }) -join ' '
# O cmd não entende as barras de escape do CommandLineToArgvW: `/s` + aspas externas entregam a linha ao cmd como foi escrita.
if ($Linha) { $linhaDeComando = '"{0}" /d /s /c "{1}"' -f $env:ComSpec, $Linha }
$job = [TetoDeCpu]::Criar($Teto, $mascara)
$primeiro = if ($Linha) { ($Linha.Trim() -split '\s+')[0] } else { $cmd[0] }
Write-Host ("com-teto-de-cpu: teto {0} % (≈ {1} de {2} threads), afinidade {3}; comando: {4}" -f $Teto, $plano.teto_em_threads, $total, $descricaoDaAfinidade, $primeiro)
# O pwsh 7 deste host é um app MSIX: o Windows o ativa FORA do job e toda a descendência dele (pytest, workers, node) fica sem teto.
# Medido em 07/10/2026: `pwsh` e o caminho real do pwsh.exe usaram 0,0 s de CPU dentro do job, `powershell` (5.1) e python direto, 100 %.
$textoDoComando = if ($Linha) { $Linha } else { $cmd -join ' ' }
$usaPwsh = $textoDoComando -match '(?i)(^|[\s"\\/&|;(])pwsh(\.exe)?(["\s]|$)'
if ($usaPwsh) {
  if (-not $PermitirPwsh) {
    # Recusa: um funil inteiro já rodou "sob teto" sem teto nenhum (58). `-PermitirPwsh` existe só para teste e para quem sabe o que faz.
    Write-Host 'com-teto-de-cpu: RECUSADO: o comando usa `pwsh` (PowerShell 7, app MSIX): ele escapa do job e o teto NÃO valeria para ele nem para os filhos. Use `powershell` (5.1) ou chame o python direto (-PermitirPwsh ignora esta trava).'
    exit 125
  }
  Write-Host 'com-teto-de-cpu: AVISO: o comando usa `pwsh` (-PermitirPwsh): o teto NÃO vale para ele nem para os filhos.'
}
$relogio = [Diagnostics.Stopwatch]::StartNew()
$zeroAvisado = $false
$tetoAtual = $Teto
$ultimaBatida = 0.0
$arquivoRuimAvisado = $false
$falhaDeTeto = $false
$batimento = [Action]{
  $tAgora = $relogio.Elapsed.TotalSeconds
  if ($ArquivoDeTeto -and (Test-Path -LiteralPath $ArquivoDeTeto)) {
    # Teto por etapa: quem encadeia as etapas escreve o percentual novo neste arquivo; vale na hora, inclusive para o que já roda.
    $lido = ''
    try { $lido = (Get-Content -LiteralPath $ArquivoDeTeto -TotalCount 1 -ErrorAction Stop) } catch { }
    $novo = 0
    if ([int]::TryParse(([string]$lido).Trim(), [ref]$novo) -and $novo -ge 1 -and $novo -le 100) {
      if ($novo -ne $script:tetoAtual) {
        try {
          [TetoDeCpu]::AjustarTeto($job, $novo)
          Write-Host ('com-teto-de-cpu: {0:F0} s: teto agora {1} % (era {2} %)' -f $tAgora, $novo, $script:tetoAtual)
          $script:tetoAtual = $novo
        } catch {
          # O teto pedido NAO foi aplicado: o comando segue sob o teto anterior e a execução não pode sair verde.
          $script:falhaDeTeto = $true
          Write-Host ('com-teto-de-cpu: REPROVADO: não consegui trocar o teto para {0} % ({1}); o comando segue com {2} % e o código de saída será 124.' -f $novo, $_.Exception.Message, $script:tetoAtual)
        }
      }
    } elseif (-not $script:arquivoRuimAvisado -and ([string]$lido).Trim()) {
      $script:arquivoRuimAvisado = $true
      Write-Host 'com-teto-de-cpu: AVISO: o arquivo de teto não tem um percentual de 1 a 100; ignorado.'
    }
  }
  if ($BatimentoS -le 0 -or ($tAgora - $script:ultimaBatida) -lt $BatimentoS - 0.5) { return }
  $script:ultimaBatida = $tAgora
  $cpuAgora = [TetoDeCpu]::CpuDoJob($job)
  Write-Host ('com-teto-de-cpu: {0:F0} s: árvore {1:F1} s de CPU ({2:F1} % do total; teto {3} %)' -f $tAgora, $cpuAgora, (100 * $cpuAgora / ($tAgora * $total)), $script:tetoAtual)
  if (-not $script:zeroAvisado -and $tAgora -ge $ZeroAposS -and $cpuAgora -ge 0 -and $cpuAgora -lt 0.2) {
    $script:zeroAvisado = $true
    Write-Host ('com-teto-de-cpu: AVISO: a árvore marca 0 s de CPU depois de {0:F0} s: o comando provavelmente está FORA do job (pwsh 7?) e roda SEM teto.' -f $tAgora)
  }
}
$tique = if ($ArquivoDeTeto) { 2000 } elseif ($BatimentoS -gt 0) { $BatimentoS * 1000 } else { 0 }
try { $codigo = [TetoDeCpu]::Rodar($job, $linhaDeComando, $tique, $batimento) }
catch { Write-Host ("com-teto-de-cpu: o comando não rodou ({0})" -f $_.Exception.Message); $codigo = 126 }
$parede = $relogio.Elapsed.TotalSeconds
$cpuDaArvore = [TetoDeCpu]::CpuDoJob($job)
if ($cpuDaArvore -ge 0 -and $parede -gt 0) {
  Write-Host ("com-teto-de-cpu: a árvore usou {0:F1} s de CPU em {1:F1} s de relógio = {2:F1} % do total de {3} threads (teto {4} %); código de saída {5}" -f `
              $cpuDaArvore, $parede, (100 * $cpuDaArvore / ($parede * $total)), $total, $tetoAtual, $codigo)
}
if ($cpuDaArvore -ge 0 -and $parede -gt 10 -and $cpuDaArvore -lt 0.5) {
  Write-Host 'com-teto-de-cpu: AVISO: a árvore quase não usou CPU: o comando provavelmente escapou do job (pwsh 7?) e rodou SEM teto.'
}
if ($falhaDeTeto -and $codigo -eq 0) { $codigo = 124 }
exit $codigo
