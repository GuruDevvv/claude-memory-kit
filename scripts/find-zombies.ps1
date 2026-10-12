<#
.SYNOPSIS
  Ищет процессы-зомби, оставшиеся от прошлых сессий агента (Claude Code, Codex). Только Windows.

.DESCRIPTION
  Зомби = процесс, который (а) похож на инструмент/сервер, запущенный агентом,
  и (б) ОСИРОТЕЛ — в его цепочке родителей нет ЖИВОГО агента (claude.exe, codex.exe).

  Второй признак и есть главный. Он отличает мусор от рабочих MCP-серверов
  текущей сессии, не полагаясь на имя процесса (python/node нельзя фильтровать
  по имени — под ними работают и MCP-сервера, и брошенные http.server).

.PARAMETER Kill
  Прибить найденное. Без флага — только показать (режим по умолчанию).

.PARAMETER MinAgeHours
  Показывать/убивать только сирот старше N часов. По умолчанию 0 (все).

.PARAMETER AgentNames
  Имена процессов агента. Их живые потомки зомби не считаются.

.EXAMPLE
  # посмотреть всё
  powershell -NoProfile -File find-zombies.ps1
  # ритуал «стоп»: явные зомби (старше суток) — сразу в утиль
  powershell -NoProfile -File find-zombies.ps1 -Kill -MinAgeHours 24
#>
param(
    [switch]$Kill,
    [double]$MinAgeHours = 0,
    [string[]]$AgentNames = @('claude.exe', 'codex.exe')
)

$ErrorActionPreference = 'Stop'

# --- 1. Снимок всех процессов -------------------------------------------------
$all = Get-CimInstance Win32_Process |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine, CreationDate
$byPid = @{}
foreach ($p in $all) { $byPid[[int]$p.ProcessId] = $p }

# --- 2. Жив ли предок-агент ---------------------------------------------------
# PID переиспользуются, поэтому родитель засчитывается только если он СТАРШЕ ребёнка.
function Test-HasLiveAgentAncestor {
    param($proc)
    $cur = $proc
    for ($i = 0; $i -lt 12; $i++) {
        $ppid = [int]$cur.ParentProcessId
        if ($ppid -le 4) { return $false }
        $parent = $byPid[$ppid]
        if (-not $parent) { return $false }                       # родитель мёртв -> сирота
        if ($parent.CreationDate -gt $cur.CreationDate) { return $false }  # PID переиспользован
        if ($AgentNames -contains $parent.Name) { return $true }
        $cur = $parent
    }
    return $false
}

# --- 3. Сигнатуры того, что вообще может быть мусором -------------------------
$shellTools = 'bash.exe', 'tail.exe', 'grep.exe', 'sed.exe', 'awk.exe', 'head.exe'

$serverPatterns = @(
    'http\.server', 'http-server', '\bserve\b',
    '\bnpm\b.*\b(run\s+)?(dev|start|serve)\b',
    '\bnext\s+dev\b', '\bvite\b', '\b(yarn|pnpm|bun)\s+dev\b',
    '\buvicorn\b', '\bgunicorn\b', '\bflask\s+run\b', '\bstreamlit\s+run\b',
    '\bngrok\b', '\bwebpack(-dev-server)?\b'
)
$serverRegex = ($serverPatterns -join '|')

# --- 4. Отбор -----------------------------------------------------------------
$suspects = foreach ($p in $all) {
    $cmd = if ($p.CommandLine) { $p.CommandLine -replace '\s+', ' ' } else { '' }
    $why = $null
    if ($shellTools -contains $p.Name) { $why = 'shell-tool' }
    elseif ($cmd -match $serverRegex)  { $why = 'dev-server' }
    if (-not $why) { continue }
    if (Test-HasLiveAgentAncestor $p) { continue }   # процесс живой сессии — не трогаем
    $ageH = (New-TimeSpan -Start $p.CreationDate).TotalHours
    if ($ageH -lt $MinAgeHours) { continue }
    [PSCustomObject]@{
        PID     = [int]$p.ProcessId
        Name    = $p.Name
        Why     = $why
        Age     = '{0:0.0} ч' -f $ageH
        Started = $p.CreationDate
        Cmd     = if ($cmd.Length -gt 90) { $cmd.Substring(0, 90) + '…' } else { $cmd }
    }
}
$suspects = @($suspects | Sort-Object Started)

# --- 5. Вывод -----------------------------------------------------------------
if ($suspects.Count -eq 0) {
    Write-Output 'Зомби-процессов не найдено.'
    return
}

# какие порты они держат
$listen = @{}
try {
    foreach ($c in (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
        $listen[[int]$c.OwningProcess] = $c.LocalPort
    }
} catch { }

$suspects |
    Select-Object PID, Name, Why, Age,
                  @{n = 'Port'; e = { if ($listen.ContainsKey($_.PID)) { $listen[$_.PID] } else { '-' } } },
                  Cmd |
    Format-Table -AutoSize -Wrap

Write-Output ''
if ($Kill) {
    Write-Output ("Найдено: {0}. Убиваю (-Kill)." -f $suspects.Count)
    Write-Output ''
    foreach ($s in $suspects) {
        try {
            Stop-Process -Id $s.PID -Force -ErrorAction Stop
            Write-Output ("  убит {0} ({1})" -f $s.PID, $s.Name)
        } catch {
            Write-Output ("  уже завершился {0} ({1})" -f $s.PID, $s.Name)
        }
    }
} else {
    Write-Output ("Найдено: {0}. НЕ убито — решает владелец (перезапуск с -Kill)." -f $suspects.Count)
}
