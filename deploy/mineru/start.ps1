# 部署层：从项目目录启动本机MinerU，模型独立保存，日志由启动终端重定向。
$ErrorActionPreference = 'Stop'
$env:MINERU_HOME = Join-Path $PSScriptRoot '.runtime'
$env:MINERU_MODEL_SOURCE = 'local'
$env:MINERU_MODEL_SMALL_BACKEND = 'onnx'
$env:MINERU_MODEL_VLM_ENGINE = 'llama-cpp'
$env:MINERU_MODEL_VLM_MAX_CONCURRENCY = '2'
$env:MINERU_PROCESSING_WINDOW_SIZE = '4'
$env:MINERU_INTRA_OP_NUM_THREADS = '4'
$env:MINERU_INTER_OP_NUM_THREADS = '1'
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Force $env:MINERU_HOME | Out-Null
Push-Location $env:MINERU_HOME
try {
    & "$PSScriptRoot/.venv/Scripts/mineru-kit.exe" api-server --host 127.0.0.1 --port 8010 --tier standard --concurrency 1 --max-inline-bytes 30000000 --upload-dir "$env:MINERU_HOME/api" --preload-models
    $mineruExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $mineruExitCode
