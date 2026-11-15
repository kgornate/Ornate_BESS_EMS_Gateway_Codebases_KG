param([Parameter(Mandatory=$true)][string]$FlutterRoot)
$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "flutter_payload\lib\core\state\gateway_controller.dart"
$target = Join-Path $FlutterRoot "lib\core\state\gateway_controller.dart"
if (!(Test-Path $target)) { throw "Flutter target not found: $target" }
$actual = (Get-FileHash -Algorithm SHA256 $target).Hash.ToLower()
$expected = "1a0943a8b97a9536782da5de6f0c9b72a4f13c2183d9c68e3feedac4fc554381"
if ($actual -ne $expected) { throw "Flutter baseline mismatch. Expected $expected, found $actual" }
$backup = "$target.phase5_2_backup_$(Get-Date -Format yyyyMMddTHHmmss)"
Copy-Item $target $backup
Copy-Item $source $target -Force
Write-Host "Updated lib\core\state\gateway_controller.dart"
Write-Host "Backup: $backup"
Write-Host "Run: dart format lib\core\state\gateway_controller.dart"
Write-Host "Then: flutter analyze"
