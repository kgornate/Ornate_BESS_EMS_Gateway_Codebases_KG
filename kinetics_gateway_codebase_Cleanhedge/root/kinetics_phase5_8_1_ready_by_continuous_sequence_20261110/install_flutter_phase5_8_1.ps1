param(
  [string]$ProjectRoot = "C:\Users\kunal\Documents\Ornate_Projects\Ornate_BESS_EMS_Gateway_Codebases_KG\kinetics_flutter_dashboard_Cleanhedge"
)

$ErrorActionPreference = "Stop"
$payload = Join-Path $PSScriptRoot "flutter_payload"
$pubspec = Join-Path $ProjectRoot "pubspec.yaml"
if (-not (Test-Path $pubspec)) {
  throw "Flutter project not found at $ProjectRoot"
}

$files = [ordered]@{
  "lib\core\api\gateway_api_client.dart" = "45980dc057b4ba57739797bca1dc488b9d66adf9305c0b9ddc596e917521dc40"
  "lib\core\state\gateway_controller.dart" = "4355f9bb4b050022381165dbb724a286ce8c6399378c4cb32ed9d2464903cc2f"
  "lib\models\gateway_models.dart" = "f328476b6450b455439bc3d01d83e19c1ec922a261242a323d32a681b4134b5e"
  "lib\screens\automation_screen.dart" = "aab11232c86b840dffcc4d24e988a3f177257a682c1950b8abdfa1c4122f8aff"
  "lib\screens\control_sequence_screen.dart" = "2782d87de7389280a4f3ebb88039c0281c3c2be3730f59bbaa288d515f1a6751"
  "lib\screens\home_shell.dart" = "0fdcf82a6c2700ca2651cbf15ea3c0619cb5f69bab92babe355b3fa3046acd0b"
}

foreach ($entry in $files.GetEnumerator()) {
  $target = Join-Path $ProjectRoot $entry.Key
  if (-not (Test-Path $target)) {
    throw "Missing Phase 5.8 Flutter baseline file: $target"
  }
  $actual = (Get-FileHash -Algorithm SHA256 $target).Hash.ToLowerInvariant()
  if ($actual -ne $entry.Value) {
    throw "Phase 5.8 baseline mismatch: $target`nExpected $($entry.Value)`nActual   $actual"
  }
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $ProjectRoot "phase5_8_1_flutter_backup_$stamp"
foreach ($relative in $files.Keys) {
  $target = Join-Path $ProjectRoot $relative
  $saved = Join-Path $backup $relative
  New-Item -ItemType Directory -Force -Path (Split-Path $saved) | Out-Null
  Copy-Item -Force $target $saved
}

foreach ($relative in $files.Keys) {
  $source = Join-Path $payload $relative
  $target = Join-Path $ProjectRoot $relative
  Copy-Item -Force $source $target
}

Write-Host "Phase 5.8.1 Flutter payload installed."
Write-Host "Backup: $backup"
Write-Host "Next: cd `"$ProjectRoot`"; dart format lib/core/api/gateway_api_client.dart lib/core/state/gateway_controller.dart lib/models/gateway_models.dart lib/screens/automation_screen.dart lib/screens/control_sequence_screen.dart lib/screens/home_shell.dart; flutter analyze; flutter run -d windows"
