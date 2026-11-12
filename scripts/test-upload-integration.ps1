$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $root '.env') -Encoding UTF8) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $config[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
}
$mysql = 'C:\Program Files\MySQL\MySQL Workbench 8.0 CE\mysql.exe'
if (!(Test-Path -LiteralPath $mysql)) { throw 'MySQL client is required for the disposable integration database.' }
$dbHost = '127.0.0.1'; $dbPort = '3307'
if ($config['DB_URL'] -match '^jdbc:mysql://([^/:]+):(\d+)/') { $dbHost = $Matches[1]; $dbPort = $Matches[2] }
$dbUser = $config['DB_USERNAME']; if (!$dbUser) { $dbUser = 'root' }
$testPassword = $config['DB_PASSWORD']
if ($config['MYSQL_ROOT_PASSWORD']) { $dbUser = 'root'; $testPassword = $config['MYSQL_ROOT_PASSWORD'] }
$env:MYSQL_PWD = $testPassword
$database = 'upload_integrity_test_' + [Guid]::NewGuid().ToString('N')
if ($database -notmatch '^upload_integrity_test_[a-f0-9]+$') { throw 'Unsafe disposable schema name.' }
$mysqlArgs = @('--connect-timeout=5', '-h', $dbHost, '-P', $dbPort, '-u', $dbUser)
& $mysql @mysqlArgs '-e' "CREATE DATABASE $database CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated upload test database.' }
try {
    $env:UPLOAD_TEST_DB_URL = 'jdbc:mysql://' + $dbHost + ':' + $dbPort + '/' + $database + '?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true'
    $env:UPLOAD_TEST_DB_USER = $dbUser
    $env:UPLOAD_TEST_DB_PASSWORD = $testPassword
    $env:JAVA_HOME = 'C:\Program Files\Java\jdk-24'
    $env:Path = "$env:JAVA_HOME\bin;$env:Path"
    Push-Location (Join-Path $root 'server')
    try {
        & mvn -q '-Dtest=UploadIntegrityRepositoryIntegrationTest' test
        if ($LASTEXITCODE -ne 0) { throw 'Upload integration tests failed.' }
        [xml]$report = Get-Content -LiteralPath 'target\surefire-reports\TEST-com.example.server.document.UploadIntegrityRepositoryIntegrationTest.xml' -Raw
        $artifactDir = Join-Path $root 'evaluation\upload-verification-20261023'
        New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
        [pscustomobject]@{ tests=[int]$report.testsuite.tests; failures=[int]$report.testsuite.failures; errors=[int]$report.testsuite.errors; skipped=[int]$report.testsuite.skipped; isolatedMySql=$true } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $artifactDir 'mysql-integration.json') -Encoding UTF8
    } finally { Pop-Location }
} finally {
    if ($database -notmatch '^upload_integrity_test_[a-f0-9]+$') { throw 'Unsafe cleanup schema.' }
    & $mysql @mysqlArgs '-e' "DROP DATABASE $database"
    Remove-Item Env:UPLOAD_TEST_DB_URL,Env:UPLOAD_TEST_DB_USER,Env:UPLOAD_TEST_DB_PASSWORD,Env:MYSQL_PWD -ErrorAction SilentlyContinue
}
