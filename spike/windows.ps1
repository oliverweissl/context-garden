$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
"== OS"; [Environment]::OSVersion.VersionString
"== firewall profiles"; Get-NetFirewallProfile | Format-Table Name, Enabled, DefaultOutboundAction -AutoSize | Out-String
"== admin?"; ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$proxy = Start-Process python -ArgumentList "proxy.py","47101","$env:TEMP\proxy.log" -PassThru -WindowStyle Hidden
$other = Start-Process python -ArgumentList "other.py" -PassThru -WindowStyle Hidden
Start-Sleep 2
New-Item -ItemType Directory C:\cgspike -Force | Out-Null
icacls C:\cgspike /grant "Everyone:(OI)(CI)F" | Out-Null
$pw = "Cg!" + [Guid]::NewGuid().ToString("N").Substring(0,20)
$sec = ConvertTo-SecureString $pw -AsPlainText -Force
New-LocalUser -Name cgsandbox -Password $sec -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
$sid = (Get-LocalUser cgsandbox).SID.Value
"sandbox user SID $sid"
$cred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\cgsandbox", $sec)
$probe = (Resolve-Path probe.py).Path
$py = (Get-Command python).Source
$probeArgs = ('"{0}" 47101 47102' -f $probe)
function As-Sandbox($exe, $argline, $label) {
  $out = "C:\cgspike\out-$([Guid]::NewGuid().ToString('N')).txt"
  $p = Start-Process -FilePath $exe -ArgumentList $argline -Credential $cred -Wait -PassThru -NoNewWindow -RedirectStandardOutput $out -RedirectStandardError "$out.err" -WorkingDirectory C:\cgspike
  "== $label (exit $($p.ExitCode))"; Get-Content $out -ErrorAction SilentlyContinue; Get-Content "$out.err" -ErrorAction SilentlyContinue | Select-Object -First 5
}
icacls $PSScriptRoot /grant "cgsandbox:(OI)(CI)RX" | Out-Null
icacls (Split-Path (Split-Path $py)) /grant "cgsandbox:(OI)(CI)RX" /T /Q | Out-Null
As-Sandbox $py $probeArgs "probe as sandbox user, before firewall rules"
$sddl = "D:(A;;CC;;;$sid)"
New-NetFirewallRule -DisplayName "cg-sandbox-block-remote" -Direction Outbound -Action Block -LocalUser $sddl -RemoteAddress @("0.0.0.0-126.255.255.255","128.0.0.0-255.255.255.255","::-::0","::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff") | Out-Null
New-NetFirewallRule -DisplayName "cg-sandbox-block-loopback-ports" -Direction Outbound -Action Block -LocalUser $sddl -Protocol TCP -RemoteAddress @("127.0.0.0/8") -RemotePort @("1-47100","47102-65535") | Out-Null
New-NetFirewallRule -DisplayName "cg-sandbox-block-loopback-udp" -Direction Outbound -Action Block -LocalUser $sddl -Protocol UDP -RemoteAddress @("127.0.0.0/8") | Out-Null
Start-Sleep 2
As-Sandbox $py $probeArgs "probe as sandbox user, with firewall rules"
As-Sandbox "curl.exe" "-sS -m 10 -o NUL -w %{http_code} https://example.com" "curl direct as sandbox user"
As-Sandbox "curl.exe" "-sS -m 20 -x http://127.0.0.1:47101 -o NUL -w %{http_code} https://example.com" "curl via proxy as sandbox user"
As-Sandbox "cmd.exe" "/c nslookup example.com 1.1.1.1" "nslookup 1.1.1.1 as sandbox user"
As-Sandbox "cmd.exe" "/c ping -n 1 -w 2000 1.1.1.1" "ping as sandbox user"
As-Sandbox "cmd.exe" "/c curl.exe -sS -m 8 -o NUL -w %{http_code} https://example.com" "child process (cmd -> curl) as sandbox user"
"== firewall rules accepted"; Get-NetFirewallRule -DisplayName "cg-sandbox-*" | Format-Table DisplayName, Enabled, Action -AutoSize | Out-String
"== the runner user is unaffected"; curl.exe -sS -m 10 -o NUL -w "%{http_code}" https://example.com
"== proxy log"; Get-Content "$env:TEMP\proxy.log" -ErrorAction SilentlyContinue
"== inbound rule for the user"
New-NetFirewallRule -DisplayName "cg-sandbox-block-inbound" -Direction Inbound -Action Block -LocalUser $sddl | Out-Null
Get-NetFirewallRule -DisplayName "cg-sandbox-block-inbound" | Format-Table DisplayName, Enabled -AutoSize | Out-String
Add-Type -Path (Join-Path $PSScriptRoot "Pty.cs")
"== ConPTY, same user (control)"
[Pty]::Run("self", "", "", "cmd.exe /c echo HELLO-PTY & whoami")
"== ConPTY handed to CreateProcessWithLogonW"
[Pty]::Run("logon", "cgsandbox", $pw, "cmd.exe /c echo HELLO-PTY & whoami")
"== ConPTY with LogonUser + CreateProcessAsUserW (runner is an elevated admin)"
[Pty]::Run("asuser", "cgsandbox", $pw, "cmd.exe /c echo HELLO-PTY & whoami")
"== relay idea: a process running AS the sandbox user hosts its own ConPTY"
icacls C:\cgspike /grant "Everyone:(OI)(CI)F" | Out-Null
Copy-Item Pty.cs, inner.ps1 C:\cgspike\
As-Sandbox (Get-Command pwsh).Source "-NoProfile -File C:\cgspike\inner.ps1" "pwsh as sandbox user running the ConPTY harness"
Get-Content C:\cgspike\inner.txt -ErrorAction SilentlyContinue
"== whoami privileges of the runner"; whoami /priv | Select-String "AssignPrimary|IncreaseQuota|Impersonate"
Stop-Process -Id $proxy.Id, $other.Id -ErrorAction SilentlyContinue
exit 0
