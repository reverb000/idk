param(
    [ValidateSet("ollama", "openai")][string]$Backend = "ollama",
    [string]$Model = "",
    [ValidateSet("3.14", "3.12")][string]$PythonVersion = "3.14"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Test-Path .\.venv\Scripts\python.exe) {
    $ExistingPythonOutput = & .\.venv\Scripts\python.exe --version
    if ($LASTEXITCODE -ne 0) { throw "Existing Python environment failed. Extract the program into a fresh folder." }
    if ($ExistingPythonOutput -notmatch '^Python (\d+\.\d+)\.') {
        throw "Could not identify the existing Python environment. Extract the program into a fresh folder."
    }
    $ExistingPythonVersion = $Matches[1]
    if ($ExistingPythonVersion -ne $PythonVersion) {
        throw "Existing .venv uses Python $ExistingPythonVersion, but setup selects $PythonVersion. Extract this ZIP into a fresh folder, or use -PythonVersion 3.12 to retain a 3.12 installation."
    }
}
py "-$PythonVersion" -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.14.8 (64-bit) from python.org, including the Python launcher. Python 3.12 can also be selected with -PythonVersion 3.12." }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = if ($Backend -eq "ollama") { "qwen2.5:7b" } else { "gpt-4.1-mini" }
}
# Merge only backend/model; retain existing capture and volume settings.
& .\.venv\Scripts\python.exe -m question_volume --configure-backend $Backend --model $Model
if ($LASTEXITCODE -ne 0) { throw "Configuration update failed." }
if ($Backend -eq "openai") {
    & .\.venv\Scripts\python.exe -m question_volume --set-api-key
    if ($LASTEXITCODE -ne 0) { throw "API key storage failed." }
}
& .\.venv\Scripts\python.exe -m question_volume --check
if ($LASTEXITCODE -ne 0) { throw "Local validation failed. Check Tesseract, taskbar, and Ollama/model installation (or OpenAI key)." }

# Shortcuts launch pythonw directly, so starting/stopping opens no console window.
$shell = New-Object -ComObject WScript.Shell
foreach ($name in @("start", "stop")) {
    $link = $shell.CreateShortcut((Join-Path $PSScriptRoot "$name.lnk"))
    $link.TargetPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
    $link.Arguments = '"' + (Join-Path $PSScriptRoot "$name.pyw") + '"'
    $link.WorkingDirectory = $PSScriptRoot
    $link.Save()
}
Write-Host "Setup complete. Double-click start.lnk to run and stop.lnk to stop."
