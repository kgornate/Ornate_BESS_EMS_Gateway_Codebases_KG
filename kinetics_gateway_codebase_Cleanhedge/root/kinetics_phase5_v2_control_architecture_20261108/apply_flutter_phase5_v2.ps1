param(
  [Parameter(Mandatory=$true)][string]$FlutterRoot
)
$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Targets = @(
  @{
    Relative = "lib\core\state\gateway_controller.dart"
    Expected = "47BF71F440BDC2EA3C0B90C8E0E2BE657472E48458D2EC4D650378D4B74BD0CF"
  },
  @{
    Relative = "lib\screens\control_sequence_screen.dart"
    Expected = "DD237C27A00805D8FE26D5BCCDAE4D8D30CB540C80211CF206EE1897401611A0"
  }
)
$Stamp = Get-Date -Format yyyyMMddTHHmmss
foreach ($Item in $Targets) {
  $Target = Join-Path $FlutterRoot $Item.Relative
  $Payload = Join-Path $PackageDir ("flutter_payload\" + $Item.Relative)
  if (!(Test-Path $Target)) { throw "Flutter target not found: $Target" }
  $Actual = (Get-FileHash -Algorithm SHA256 $Target).Hash
  if ($Actual -ne $Item.Expected) {
    throw "Flutter baseline mismatch for $($Item.Relative). Expected $($Item.Expected), found $Actual"
  }
  Copy-Item $Target "$Target.phase5-v2-backup-$Stamp"
  Copy-Item $Payload $Target -Force
  Write-Host "Updated $($Item.Relative)"
}
Write-Host "Flutter Phase 5 v2 sources applied."
Write-Host "Now run: dart format lib/core/state/gateway_controller.dart lib/screens/control_sequence_screen.dart"
Write-Host "Then run: flutter analyze"
Write-Host "Finally rebuild/restart the Flutter dashboard."
