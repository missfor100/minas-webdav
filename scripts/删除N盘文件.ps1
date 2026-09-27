# 删除 N: 盘上用资源管理器删不掉的文件
param([Parameter(Mandatory=$true)][string]$Path)
$py = if ($env:MIMO_PYTHON) { $env:MIMO_PYTHON } else { "python" }
$full = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path "N:\" $Path }
if (-not (Test-Path $full)) { Write-Host "不存在: $full"; exit 1 }
$rel = $full.Replace("N:\", "").Replace("\", "/")
$code = @"
import http.client, urllib.parse, sys
path = '/pool0/data/' + urllib.parse.quote(sys.argv[1], safe='/')
c = http.client.HTTPConnection('127.0.0.1', 8899, timeout=30)
c.request('DELETE', path, headers={'Connection':'close'})
r = c.getresponse(); r.read()
print('DELETE', r.status, sys.argv[1])
sys.exit(0 if r.status in (200,204) else 1)
"@
$tmp = Join-Path $env:TEMP "minas_del.py"
Set-Content -Path $tmp -Value $code -Encoding UTF8
& $py $tmp $rel
