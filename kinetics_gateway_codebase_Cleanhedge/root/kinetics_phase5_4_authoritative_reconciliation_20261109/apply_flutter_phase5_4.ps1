param([Parameter(Mandatory=$true)][string]$FlutterRoot)
$ErrorActionPreference = "Stop"
$relative = "lib\core\state\gateway_controller.dart"
$source = Join-Path $PSScriptRoot ("flutter_payload\" + $relative)
$target = Join-Path $FlutterRoot $relative
if (!(Test-Path $target)) { throw "Flutter target not found: $target" }
$text = Get-Content $target -Raw
if (!$text.Contains("_startReconciliationTracking") -or !$text.Contains("_pairStatusEpochs")) {
  throw "Flutter Phase 5.3 baseline markers not found; do not force-copy this patch."
}
$stamp = Get-Date -Format yyyyMMddTHHmmss
Copy-Item $target "$target.phase5_4_backup_$stamp"
Copy-Item $source $target -Force
Write-Host "Updated $relative"
Write-Host "Run: dart format lib/core/state/gateway_controller.dart"
Write-Host "Then: flutter analyze"
