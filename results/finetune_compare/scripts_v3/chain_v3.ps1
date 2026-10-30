# v3 comparison: base (Ollama), r7, r8 (llama-server, one at a time). Sandbox only.
$ErrorActionPreference = "Continue"
Set-Location C:\Users\kerim\Projects\local-assistant
$py = ".\.venv\Scripts\python.exe"
$S = "C:\Users\kerim\AppData\Local\Temp\claude\C--Users-kerim-Projects\a449e780-d4b1-40d1-ab4d-d387b76cca30\scratchpad\compare_run.py"
$LL = "C:\Users\kerim\Projects\tools\llama.cpp"
$env:PYTHONIOENCODING = "utf-8"

Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
$env:LLM_BACKEND = "ollama"
"=== v3_base (Ollama) $(Get-Date -Format HH:mm:ss)"
& $py $S v3_base --ollama --evals test_r8,test_r7
if ($LASTEXITCODE -ne 0) { "base run failed: $LASTEXITCODE"; exit 1 }
ollama stop qwen3.5:4b-q4_K_M

foreach ($m in @("r7", "r8")) {
    $env:LLM_BACKEND = "llama-server"
    $log = "$LL\server_v3_$m.log"
    Remove-Item $log, "$log.out" -ErrorAction SilentlyContinue
    $srv = Start-Process -FilePath "$LL\bin\llama-server.exe" -PassThru -WindowStyle Hidden `
        -ArgumentList "-m `"C:\Users\kerim\Projects\local-assistant\models\assistant-$m.Q4_K_M.gguf`" --jinja -ngl 99 -c 8192 --host 127.0.0.1 --port 8080" `
        -RedirectStandardError $log -RedirectStandardOutput "$log.out"
    for ($i = 0; $i -lt 300; $i++) {
        if ((Test-Path $log) -and (Select-String -Path $log -Pattern "listening on" -Quiet)) { break }
        Start-Sleep -Milliseconds 500
    }
    Select-String -Path $log -Pattern "using device|offloaded|listening" | ForEach-Object { $_.Line }
    "=== v3_$m (llama-server) $(Get-Date -Format HH:mm:ss)"
    & $py $S "v3_$m" --evals test_r8,test_r7
    $code = $LASTEXITCODE
    Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
    "llama-server stopped"
    if ($code -ne 0) { "$m run failed: $code"; exit 1 }
}
Remove-Item Env:LLM_BACKEND
& $py make_sandbox.py
"=== done $(Get-Date -Format HH:mm:ss)"
