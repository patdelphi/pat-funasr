<#
程序说明：
批量测试脚本：将 "test\" 目录下的音视频文件，分别用每种 ASR 模型跑一遍，
每种模型创建一个输出目录（目录名=模型名），并把 txt/srt/vtt/tsv/json/zip 输出写入对应目录。

产物结构示例：
  "test\sensevoice\1.txt" / "1.srt" / "1.vtt" / "1.tsv" / "1.json" / "1.zip"
  "test\sensevoice\run.log"  （记录调用模型的参数与命令）

注意：
- 如模型缓存不存在，FunASR 可能会触发从 ModelScope/HF 下载模型（外网）。
- 本脚本会将非 wav 输入通过 ffmpeg 转成 16k 单声道 wav，保存到模型目录下（便于复用与排查）。
#>

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$TestDir = Join-Path $Repo "test"

if (-not (Test-Path -LiteralPath "$TestDir")) {
  throw "目录不存在：""$TestDir"""
}

$Python = Join-Path $Repo "runtime\python\python.exe"
if (-not (Test-Path -LiteralPath "$Python")) {
  throw "未找到 Python 运行时：""$Python"""
}

# 关键环境变量（对齐 "run_api.bat"），确保使用本地缓存与本地依赖
$env:MODELSCOPE_CACHE = Join-Path $Repo "workspace\models"
$env:HF_HOME = Join-Path $Repo "workspace\models\huggingface"
$env:TRANSFORMERS_CACHE = Join-Path $Repo "workspace\models\transformers"
$env:PYTHONPATH = "$Repo\runtime\python;$Repo\runtime\python\Lib\site-packages;$Repo\app;$Repo\app\openai_api"
$env:PATH = "$Repo\runtime\python\Lib\site-packages\torch\lib;$env:PATH"

# 模型别名：与 API 的 "model" 参数一致
$Models = @(
  "sensevoice",
  "paraformer",
  "fun-asr-nano"
)

# 扫描 test 目录的输入文件
$Exts = @(".wav",".mp3",".flac",".m4a",".ogg",".webm",".mp4",".mkv",".avi",".mov")
$Inputs = Get-ChildItem -LiteralPath "$TestDir" -File | Where-Object { $Exts -contains $_.Extension.ToLower() } | Sort-Object Name
if (-not $Inputs) {
  throw "目录中未找到音视频文件：""$TestDir"""
}

foreach ($Model in $Models) {
  $OutDir = Join-Path $TestDir $Model
  New-Item -ItemType Directory -Force -Path "$OutDir" | Out-Null
  $LogPath = Join-Path $OutDir "run.log"
  $Runner = Join-Path $Repo "scripts\batch_transcribe.py"

  # 记录“调用命令”（PowerShell 层面的完整命令）
  $CmdLine = @(
    "`"$Python`"",
    "-u",
    "-X", "utf8",
    "`"$Runner`"",
    "--model-alias", "`"$Model`"",
    "--out-dir", "`"$OutDir`"",
    "--log-path", "`"$LogPath`""
  ) -join " "
  ("[{0}] ps_cmd={1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $CmdLine) | Out-File -LiteralPath "$LogPath" -Append -Encoding utf8

  # 实际执行（把 test 目录下所有输入文件路径传给 python）
  & "$Python" -u -X utf8 "$Runner" --model-alias "$Model" --out-dir "$OutDir" --log-path "$LogPath" @($Inputs.FullName)
}

Write-Host "OK. 输出目录：""$TestDir"""
