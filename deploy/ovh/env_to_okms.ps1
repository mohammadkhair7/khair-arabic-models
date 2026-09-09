# =============================================================================
# Load alarabia.chat's deploy SECRETS from a local .env into the OVH Secret
# Manager object `alarabia-chat/deploy`.
#
# Only genuine secrets and account-level email settings are carried. Everything
# else the app reads — the donate links, the site domain, the request ceilings —
# is public configuration and lives in deploy/ovh/host.env.example and
# deploy/docker-compose.yml, so the same OKMS object works unchanged on the
# future Server "QC".
#
# The plaintext env file exists only in the OS temp dir and on the server's
# tmpfs (/run/deploy), and both copies are shredded before the script returns.
# Nothing is ever printed except key NAMES.
#
# Usage (from the repo root, with webapp/.env filled in):
#   pwsh -File deploy/ovh/env_to_okms.ps1
# =============================================================================
param(
    [string]$Server   = "kalimat-a",
    [string]$EnvFile  = "webapp/.env",
    [string]$OkmsPath = "alarabia-chat/deploy"
)

$ErrorActionPreference = "Stop"

# SENDGRID_API_KEY is the only true credential this app has. The other two are
# not secret, but they belong with it: they are account-level settings that must
# stay consistent with the verified sender on the SendGrid account, and keeping
# all three in one object means the QC move copies one thing, not two.
$Wanted = @(
    "SENDGRID_API_KEY",
    "SENDGRID_FROM_EMAIL",
    "CONTACT_EMAIL"
)

$tmp = Join-Path $env:TEMP ("alarabia_okms_{0}.env" -f ([guid]::NewGuid().ToString('N').Substring(0,8)))

try {
    if (-not (Test-Path $EnvFile)) { throw "$EnvFile not found (copy webapp/.env.example and fill it in)" }
    Write-Host "==> reading $EnvFile"

    $found = [ordered]@{}
    foreach ($line in (Get-Content $EnvFile)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $k = $Matches[1]; $v = $Matches[2]
            # Strip surrounding quotes if the file uses them.
            if ($v -match '^"(.*)"$' -or $v -match "^'(.*)'$") { $v = $Matches[1] }
            if ($Wanted -contains $k -and $v -ne "") { $found[$k] = $v }
        }
    }

    $missing = $Wanted | Where-Object { -not $found.Contains($_) }
    Write-Host ("    carried: {0}" -f ($found.Keys -join ", "))
    if ($missing) { Write-Host ("    absent (skipped): {0}" -f ($missing -join ", ")) }
    if (-not $found.Contains("SENDGRID_API_KEY")) {
        throw "SENDGRID_API_KEY is empty - the feedback form would be disabled. Refusing to write."
    }

    # LF endings and no BOM: okms_put.py splits on the first '=' per line.
    $sb = New-Object System.Text.StringBuilder
    foreach ($k in $found.Keys) { [void]$sb.Append($k).Append('=').Append($found[$k]).Append("`n") }
    [System.IO.File]::WriteAllText($tmp, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))

    Write-Host "==> uploading to $Server tmpfs and writing OKMS object $OkmsPath"
    ssh $Server "sudo install -d -m 700 /run/deploy" | Out-Null
    scp -q $tmp "${Server}:/tmp/.ar_secrets.env"
    ssh $Server "sudo install -m 600 /tmp/.ar_secrets.env /run/deploy/ar_onboard.env && shred -u /tmp/.ar_secrets.env && sudo sh -c 'set -a; . /opt/deploy/okms.env; set +a; python3 /opt/deploy/cicd/okms_put.py $OkmsPath /run/deploy/ar_onboard.env'; sudo shred -u /run/deploy/ar_onboard.env"
    if ($LASTEXITCODE -ne 0) { throw "OKMS write failed" }

    Write-Host "==> verifying the object reads back (names only)"
    ssh $Server "sudo sh -c 'set -a; . /opt/deploy/okms.env; set +a; python3 /opt/deploy/alarabia-chat/cicd/okms_fetch.py $OkmsPath /run/deploy/ar_verify.env && cut -d= -f1 /run/deploy/ar_verify.env | paste -sd, -; shred -u /run/deploy/ar_verify.env'"
}
finally {
    if (Test-Path $tmp) {
        # Overwrite before unlinking so the plaintext does not linger in %TEMP%.
        $len = (Get-Item $tmp).Length
        [System.IO.File]::WriteAllBytes($tmp, (New-Object byte[] $len))
        Remove-Item $tmp -Force
    }
}

Write-Host "`nDone. Secrets live only in OKMS ($OkmsPath); deploy.sh fetches them just-in-time."
