param([Parameter(Mandatory=$true)][string]$FlutterRoot)
$ErrorActionPreference = "Stop"
$targets = @(
  "lib\core\state\gateway_controller.dart",
  "lib\models\gateway_models.dart",
  "lib\screens\control_sequence_screen.dart"
)
$stamp = Get-Date -Format yyyyMMddTHHmmss
foreach ($relative in $targets) {
  $source = Join-Path $PSScriptRoot ("flutter_payload\" + $relative)
  $target = Join-Path $FlutterRoot $relative
  if (!(Test-Path $target)) { throw "Flutter target not found: $target" }
  Copy-Item $target "$target.phase5_3_backup_$stamp"
  Copy-Item $source $target -Force
  Write-Host "Updated $relative"
}
Write-Host "Run: dart format lib/core/state/gateway_controller.dart lib/models/gateway_models.dart lib/screens/control_sequence_screen.dart"
Write-Host "Then: flutter analyze"

