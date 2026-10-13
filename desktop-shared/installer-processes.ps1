param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$PreviousInstallDir,
    [Parameter(Mandatory = $true)][string]$AppExecutable
)
$ErrorActionPreference = "Stop"
$installRoots = @($InstallDir, $PreviousInstallDir) | Where-Object { $_ } | ForEach-Object {
    [IO.Path]::GetFullPath($_).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
} | Select-Object -Unique
$installerSession = (Get-Process -Id $PID).SessionId
$installedExecutableNames = @($AppExecutable, "zaowu-backend.exe", "zaowu-provider.exe", "ffmpeg.exe", "ffprobe.exe", "esbuild.exe")

function Get-InstalledProcesses {
    @(Get-CimInstance -ClassName Win32_Process | Where-Object {
        $candidate = $_
        $candidate.ProcessId -ne $PID -and $candidate.SessionId -eq $installerSession -and $candidate.ExecutablePath -and
        $candidate.Name -in $installedExecutableNames -and
        @($installRoots | Where-Object { $candidate.ExecutablePath.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
    })
}

try {
    $runningClients = @(Get-InstalledProcesses)
    foreach ($installRoot in $installRoots) {
        $client = Join-Path $installRoot $AppExecutable
        if (@($runningClients | Where-Object { $_.ExecutablePath -eq $client }).Count -gt 0 -and (Test-Path -LiteralPath $client -PathType Leaf)) {
            # Single-instance clients forward --quit to their existing process.
            Start-Process -FilePath $client -ArgumentList "--quit" -WindowStyle Hidden | Out-Null
        }
    }
    $shutdownDeadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $installedProcesses = @(Get-InstalledProcesses)
        if (-not $installedProcesses.Count) { exit 0 }
        foreach ($installedProcess in $installedProcesses) {
            if ($installedProcess.Name -eq $AppExecutable) {
                $clientProcess = Get-Process -Id $installedProcess.ProcessId -ErrorAction SilentlyContinue
                if ($clientProcess) { $clientProcess.CloseMainWindow() | Out-Null }
            }
        }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $shutdownDeadline)

    # Recheck the executable path before termination; never kill by image name.
    foreach ($installedProcess in (Get-InstalledProcesses)) {
        $currentProcess = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$($installedProcess.ProcessId)"
        if ($currentProcess -and $currentProcess.ExecutablePath -eq $installedProcess.ExecutablePath) {
            Stop-Process -Id $installedProcess.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    $releaseDeadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        if (-not @(Get-InstalledProcesses).Count) { exit 0 }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $releaseDeadline)
    throw "Installed processes have not released their files."
} catch {
    Write-Output "The previous client could not be closed. Close it and retry installation."
    exit 1
}
