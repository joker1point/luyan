# Test connectivity and basic endpoints
$ErrorActionPreference = "Continue"

function Test-Endpoint {
    param([string]$Name, [string]$Method, [string]$Url, [string]$Body = $null)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        if ($Method -eq "GET") {
            $r = Invoke-WebRequest -Uri $Url -Method Get -UseBasicParsing -TimeoutSec 30 -Headers @{"Accept"="application/json"}
        } elseif ($Method -eq "DELETE") {
            $r = Invoke-WebRequest -Uri $Url -Method Delete -UseBasicParsing -TimeoutSec 30
        } else {
            $r = Invoke-WebRequest -Uri $Url -Method $Method -UseBasicParsing -TimeoutSec 30 -ContentType "application/json" -Body $Body
        }
        $sw.Stop()
        $len = if ($r.Content) { $r.Content.Length } else { 0 }
        Write-Host ("[{0}] {1} {2} - HTTP {3} - {4}ms - {5}B" -f $Name, $Method, $Url, $r.StatusCode, $sw.ElapsedMilliseconds, $len)
        return @{ status = $r.StatusCode; time = $sw.ElapsedMilliseconds; body = $r.Content; ok = $true }
    } catch {
        $sw.Stop()
        $code = $null
        $body = $null
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $body = $reader.ReadToEnd()
        }
        Write-Host ("[{0}] {1} {2} - HTTP {3} - {4}ms - ERR: {5}" -f $Name, $Method, $Url, $code, $sw.ElapsedMilliseconds, $_.Exception.Message.Substring(0, [Math]::Min(80, $_.Exception.Message.Length)))
        return @{ status = $code; time = $sw.ElapsedMilliseconds; body = $body; ok = $false; err = $_.Exception.Message }
    }
}

Write-Host "=== Connectivity ==="
Test-Endpoint "Backend Root" "GET" "http://localhost:8000/"
Test-Endpoint "OpenAPI" "GET" "http://localhost:8000/openapi.json"
Test-Endpoint "Docs" "GET" "http://localhost:8000/docs"
Test-Endpoint "Connection" "GET" "http://localhost:8000/api/test/connection"
Test-Endpoint "Characters List" "GET" "http://localhost:8000/api/characters"
Test-Endpoint "Sessions" "GET" "http://localhost:8000/api/sessions"
Test-Endpoint "Events" "GET" "http://localhost:8000/api/events"
Test-Endpoint "LLM Settings" "GET" "http://localhost:8000/api/settings/llm"
Test-Endpoint "Cache Stats" "GET" "http://localhost:8000/api/performance/char-data-cache-stats"
