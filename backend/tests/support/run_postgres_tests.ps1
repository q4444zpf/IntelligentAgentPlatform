param(
    [Parameter(Mandatory = $true)]
    [string[]]$PytestPath
)

$ErrorActionPreference = "Stop"
$iapContainerName = "iap-postgres-test-$([guid]::NewGuid().ToString('N'))"
$iapDatabase = "iap_auth_test"
$iapUser = "iap_auth_test"
$iapPassword = [guid]::NewGuid().ToString("N")
$iapContainerId = $null

try {
    docker container inspect $iapContainerName *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "Disposable container name unexpectedly exists"
    }

    $iapContainerId = (docker run --detach --name $iapContainerName --env "POSTGRES_DB=$iapDatabase" --env "POSTGRES_USER=$iapUser" --env "POSTGRES_PASSWORD=$iapPassword" --publish "127.0.0.1::5432" postgres:16-alpine).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $iapContainerId) {
        throw "Disposable PostgreSQL startup failed"
    }

    $iapReady = $false
    for ($iapAttempt = 0; $iapAttempt -lt 60; $iapAttempt++) {
        docker exec $iapContainerId pg_isready -U $iapUser -d $iapDatabase *> $null
        if ($LASTEXITCODE -eq 0) {
            $iapReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $iapReady) { throw "Disposable PostgreSQL did not become ready" }

    $iapBinding = (docker port $iapContainerId 5432/tcp).Trim()
    if ($iapBinding -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "Unsafe PostgreSQL binding: $iapBinding"
    }

    $env:PYTHONPATH = "backend"
    $env:TEST_DATABASE_URL = (
        "postgresql+psycopg://{0}:{1}@127.0.0.1:{2}/{3}" -f
        $iapUser, $iapPassword, $Matches[1], $iapDatabase
    )
    & python -m pytest -q @PytestPath
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL test run failed" }
} finally {
    if ($iapContainerId) {
        $iapActualNameOutput = @(docker inspect --format '{{.Name}}' $iapContainerId)
        $iapInspectExit = $LASTEXITCODE
        if ($iapInspectExit -ne 0) { throw "Container identity inspection failed" }
        $iapActualName = (($iapActualNameOutput -join "").Trim().TrimStart("/"))
        if ($iapActualName -ne $iapContainerName) {
            throw "Container identity changed; refusing cleanup"
        }

        docker rm --force --volumes $iapContainerId
        $iapRemoveExit = $LASTEXITCODE
        $iapRemainingContainers = @(docker ps -aq --no-trunc --filter "id=$iapContainerId")
        $iapListExit = $LASTEXITCODE
        $iapRemainingContainers = @(
            $iapRemainingContainers | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
        if ($iapRemoveExit -ne 0) { throw "Disposable PostgreSQL cleanup failed" }
        if ($iapListExit -ne 0) { throw "Failed to verify disposable PostgreSQL cleanup" }
        if ($iapRemainingContainers.Count -ne 0) {
            throw "Disposable PostgreSQL container still exists: $iapContainerId"
        }
    }
}
