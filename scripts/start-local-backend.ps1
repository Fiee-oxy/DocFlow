$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'Missing .env. Run: Copy-Item .env.example .env, then configure the local services.'
}

# Parse values as plain text; never execute .env content or print credentials.
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $entry = $line.Trim()
    if (-not $entry -or $entry.StartsWith('#')) { continue }
    if ($entry -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
    $configName = $Matches[1]
    $configValue = $Matches[2].Trim()
    if ($configValue.Length -ge 2 -and
        (($configValue.StartsWith('"') -and $configValue.EndsWith('"')) -or
         ($configValue.StartsWith("'") -and $configValue.EndsWith("'")))) {
        $configValue = $configValue.Substring(1, $configValue.Length - 2)
    }
    if ($configName -eq 'SILICONFLOW_API_KEY' -and -not $configValue) { continue }
    [Environment]::SetEnvironmentVariable($configName, $configValue, 'Process')
}

# The project requires JDK 21+. This workstation has JDK 24 installed.
if (-not $env:SPRING_PROFILES_ACTIVE) { $env:SPRING_PROFILES_ACTIVE = 'final' }
$localJdk = 'C:\Program Files\Java\jdk-24'
if (Test-Path -LiteralPath (Join-Path $localJdk 'bin\java.exe')) {
    $env:JAVA_HOME = $localJdk
}
Push-Location (Join-Path $projectRoot 'server')
try {
    # Keep Maven output separate from VS Code's incremental Java compiler.
    $buildRoot = Join-Path $projectRoot 'server/target-final'
    & mvn "-Dbuild.output.root=$buildRoot" spring-boot:run
    if ($LASTEXITCODE -ne 0) { throw 'Backend startup failed; see the Maven log above.' }
} finally {
    Pop-Location
}
