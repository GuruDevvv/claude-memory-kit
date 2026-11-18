<#
.SYNOPSIS
  Ищет процессы-зомби, оставшиеся от прошлых сессий агента (Claude Code, Codex). Только Windows.

.DESCRIPTION
  Зомби = процесс, который (а) похож на инструмент/сервер, запущенный агентом,
  и (б) ОСИРОТЕЛ — в его цепочке родителей нет ЖИВОГО агента (claude.exe, codex.exe).

  Второй признак и есть главный. Он отличает мусор от рабочих MCP-серверов
  текущей сессии, не полагаясь на имя процесса (python/node нельзя фильтровать
  по имени — под ними работают и MCP-сервера, и брошенные http.server).

  Отдельный случай — фоновый демон Codex (`codex.exe app-server ... --managed-daemon`).
  Он жив, поэтому его дети сиротами не считаются, но он поднимает полный набор
  MCP-серверов (notebooklm, hd_ai, docker gateway, duckduckgo) на каждую
  переписку и старые не гасит: за несколько дней набегают гигабайты (17.11.2026: 3 набора,
  55 процессов, 7,5 ГБ). Если один и тот же MCP-сервер (та же командная строка) запущен
  у демона несколько раз, самая свежая копия не трогается, более старые выводятся как
  codex-mcp-copy и с -KillCodexCopies гасятся вместе с потомками. Сам демон, его shell-команды,
  node_repl и codex-code-mode не трогаются.

.PARAMETER Kill
  Прибить сирот. Без флага — только показать (режим по умолчанию).
  Копии MCP у демона Codex этот флаг НЕ трогает (решение владельца 18.11.2026):
  у старой, но открытой переписки после этого пропадают MCP-инструменты.

.PARAMETER KillCodexCopies
  Закрыть лишние копии MCP у демона Codex. Только после «да» владельца.

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
    [switch]$KillCodexCopies,
    [double]$MinAgeHours = 0,
    [string[]]$AgentNames = @('claude.exe', 'codex.exe'),
    # как узнать демон Codex по командной строке
    [string]$DaemonPattern = 'app-server\b.*--managed-daemon',
    # какие дети демона считаются MCP-серверами
    [string]$McpPattern = 'mcp'
)

$ErrorActionPreference = 'Stop'

# --- 1. Снимок всех процессов -------------------------------------------------
$all = Get-CimInstance Win32_Process |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine, CreationDate, PrivatePageCount
$byPid = @{}
foreach ($p in $all) { $byPid[[int]$p.ProcessId] = $p }

# дети каждого процесса (ребёнок засчитывается, только если он моложе родителя: PID переиспользуются)
$kids = @{}
foreach ($p in $all) {
    $pp = [int]$p.ParentProcessId
    $parent = $byPid[$pp]
    if (-not $parent -or $parent.CreationDate -gt $p.CreationDate) { continue }
    if (-not $kids.ContainsKey($pp)) { $kids[$pp] = New-Object System.Collections.ArrayList }
    [void]$kids[$pp].Add($p)
}
function Get-Tree {
    # процесс и все его потомки, потомки раньше родителей (порядок для убийства)
    param([int]$procId)
    $out = New-Object System.Collections.ArrayList
    if ($kids.ContainsKey($procId)) {
        foreach ($c in $kids[$procId]) { foreach ($x in (Get-Tree ([int]$c.ProcessId))) { [void]$out.Add($x) } }
    }
    [void]$out.Add($procId)
    return $out
}

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
    elseif ($cmd -match $serverRegex -and $cmd -notmatch 'Program Files\\Docker\\') { $why = 'dev-server' }
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
        Kill    = @([int]$p.ProcessId)
    }
}

# --- 4б. Лишние копии MCP-серверов у демона Codex ------------------------------
$daemons = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -match $DaemonPattern })
$mcpCopies = foreach ($d in $daemons) {
    $did = [int]$d.ProcessId
    if (-not $kids.ContainsKey($did)) { continue }
    # только MCP-серверы: shell-команды и node_repl живой переписки сюда не попадают
    $servers = @($kids[$did] | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $McpPattern -and
        $_.Name -notmatch '^(powershell|pwsh|bash|sh)\.exe$' -and $_.CommandLine -notmatch 'codex-code-mode'
    })
    # один и тот же сервер (одна командная строка) запущен несколько раз — старые копии лишние
    foreach ($g in ($servers | Group-Object { ($_.CommandLine -replace '\s+', ' ').Trim() })) {
        if ($g.Count -lt 2) { continue }
        $copies = @($g.Group | Sort-Object CreationDate)
        for ($i = 0; $i -lt $copies.Count - 1; $i++) {   # самую свежую копию не трогаем
            $c = $copies[$i]
            $ageH = (New-TimeSpan -Start $c.CreationDate).TotalHours
            if ($ageH -lt $MinAgeHours) { continue }
            $tree = @(Get-Tree ([int]$c.ProcessId))
            $mb = 0
            foreach ($x in $tree) { if ($byPid[$x].PrivatePageCount) { $mb += [double]$byPid[$x].PrivatePageCount / 1MB } }
            $kind = if ($c.CommandLine -match '([\w.-]*mcp[\w.-]*)') { $Matches[1] } else { $c.Name }
            [PSCustomObject]@{
                PID     = [int]$c.ProcessId
                Name    = $c.Name
                Why     = 'codex-mcp-copy'
                Age     = '{0:0.0} ч' -f $ageH
                Started = $c.CreationDate
                Cmd     = '{0}: копия {1} из {2}, ~{3:N0} МБ, процессов {4} (демон Codex {5})' -f $kind, ($i + 1), $copies.Count, $mb, $tree.Count, $did
                Kill    = $tree
                MB      = $mb
            }
        }
    }
}
$suspects = @(@($suspects) + @($mcpCopies) | Where-Object { $_ } | Sort-Object Started)

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
$copies  = @($suspects | Where-Object { $_.Why -eq 'codex-mcp-copy' })
$orphans = @($suspects | Where-Object { $_.Why -ne 'codex-mcp-copy' })
$toKill  = @()
if ($Kill)            { $toKill += $orphans }
if ($KillCodexCopies) { $toKill += $copies }
if ($toKill.Count -gt 0) {
    Write-Output ("Найдено: {0}. Закрываю: {1}." -f $suspects.Count, $toKill.Count)
    Write-Output ''
    foreach ($s in $toKill) {
        $dead = 0
        foreach ($k in $s.Kill) {
            try { Stop-Process -Id $k -Force -ErrorAction Stop; $dead++ } catch { }
        }
        if ($dead -gt 0) {
            Write-Output ("  убит {0} ({1}), процессов: {2}" -f $s.PID, $s.Name, $dead)
        } else {
            Write-Output ("  уже завершился {0} ({1})" -f $s.PID, $s.Name)
        }
    }
}
if (-not $Kill -and $orphans.Count -gt 0) {
    Write-Output ("Сироты: {0}, НЕ закрыты — перезапуск с -Kill." -f $orphans.Count)
}
if (-not $KillCodexCopies -and $copies.Count -gt 0) {
    $mb = ($copies | Measure-Object -Property MB -Sum).Sum
    Write-Output ("Лишние копии MCP у Codex: {0}, ~{1:N0} МБ, НЕ закрыты. Закрываются только после «да» владельца:" -f $copies.Count, $mb)
    Write-Output '  у старой открытой переписки Codex после этого перестанут работать её MCP, пока окно не переоткрыть.'
    Write-Output '  Закрыть: тот же вызов с -KillCodexCopies (без -Kill, если сирот трогать не надо).'
}
