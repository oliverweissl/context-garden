Add-Type -Path (Join-Path $PSScriptRoot "Pty.cs")
[Pty]::Run("self", "", "", "cmd.exe /c echo HELLO-INNER-PTY & whoami & curl.exe -sS -m 8 -o NUL -w %{http_code} https://example.com") | Out-File -Encoding utf8 C:\cgspike\inner.txt
