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
  "fun-asr-nano",
  "qwen3-asr"
)

# 扫描 test 目录的输入文件
$Exts = @(".wav",".mp3",".flac",".m4a",".ogg",".webm",".mp4",".mkv",".avi",".mov")
$Inputs = Get-ChildItem -LiteralPath "$TestDir" -File | Where-Object { $Exts -contains $_.Extension.ToLower() } | Sort-Object Name
if (-not $Inputs) {
  throw "目录中未找到音视频文件：""$TestDir"""
}

# 单模型超时上限（秒）：模型加载 + 全部文件转写，超时强制终止进程
$TimeoutSec = 1800

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
  ("[{0}] ps_cmd={1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $CmdLine) | Out-File -LiteralPath "$LogPath" -Encoding utf8

  # 执行（把 test 目录下所有输入文件路径传给 python）。
  # 每个模型独立进程：进程退出即释放显存，避免多个模型串行常驻导致 OOM；
  # 单模型超时自动杀进程，失败记录后继续下一个模型，不中断整批。
  $ArgsList = @("-u", "-X", "utf8", "$Runner", "--model-alias", "$Model", "--out-dir", "$OutDir", "--log-path", "$LogPath") + @($Inputs.FullName)
  try {
    $Proc = Start-Process -FilePath "$Python" -ArgumentList $ArgsList -NoNewWindow -PassThru
    if (-not $Proc.WaitForExit($TimeoutSec * 1000)) {
      try { $Proc.Kill() } catch { }
      Write-Host ("[{0}] 模型 {1} 超时（{2}s），已强制终止。" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Model, $TimeoutSec)
    } else {
      Write-Host ("[{0}] 模型 {1} 完成，退出码 {2}。" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Model, $Proc.ExitCode)
    }
  } catch {
    Write-Host ("[{0}] 模型 {1} 执行失败：{2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Model, $_.Exception.Message)
  }
}

Write-Host "OK. 输出目录：""$TestDir"""
