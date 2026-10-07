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
  [string]$ComandoJson = ''
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

  // Roda `linha` (já com aspas de CreateProcess) debaixo do job: o processo nasce SUSPENSO, entra no job e só então roda, de modo que
  // nem ele nem nenhum descendente passa um instante fora do teto. Devolve o código de saída do comando; espera o processo acabar.
  public static int Rodar(IntPtr job, string linha) {
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
      WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
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
$relogio = [Diagnostics.Stopwatch]::StartNew()
try { $codigo = [TetoDeCpu]::Rodar($job, $linhaDeComando) }
catch { Write-Host ("com-teto-de-cpu: o comando não rodou ({0})" -f $_.Exception.Message); $codigo = 126 }
$parede = $relogio.Elapsed.TotalSeconds
$cpuDaArvore = [TetoDeCpu]::CpuDoJob($job)
if ($cpuDaArvore -ge 0 -and $parede -gt 0) {
  Write-Host ("com-teto-de-cpu: a árvore usou {0:F1} s de CPU em {1:F1} s de relógio = {2:F1} % do total de {3} threads (teto {4} %); código de saída {5}" -f `
              $cpuDaArvore, $parede, (100 * $cpuDaArvore / ($parede * $total)), $total, $Teto, $codigo)
}
exit $codigo
