param(
  [Parameter(Mandatory=$true)][string]$FlutterRoot
)

$ErrorActionPreference = "Stop"
$Target = Join-Path $FlutterRoot "lib\core\state\gateway_controller.dart"
$Payload = Join-Path $PSScriptRoot "flutter_payload\lib\core\state\gateway_controller.dart"
if (!(Test-Path $Target)) { throw "Flutter target not found: $Target" }
if (!(Test-Path $Payload)) { throw "Patch payload not found: $Payload" }

$Expected = "08a27716ed868bd57741e4ef881cf07b5f9375c081b6856719cac61e4514e29a"
$Actual = (Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) {
  throw "Unexpected Flutter baseline ($Actual). Expected installed Phase 5.4 ($Expected); no file was changed."
}

$Stamp = Get-Date -Format "yyyyMMddTHHmmss"
$Backup = "$Target.phase5_5_backup_$Stamp"
Copy-Item -LiteralPath $Target -Destination $Backup
Copy-Item -LiteralPath $Payload -Destination $Target -Force
Write-Host "Flutter Phase 5.5 applied."
Write-Host "Backup: $Backup"
Write-Host "Next: dart format lib\core\state\gateway_controller.dart"
Write-Host "Then: flutter analyze"
