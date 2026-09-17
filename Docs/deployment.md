#
本地运行与部署建议（Deployment）

## 推荐启动方式

使用 "FunASR_pat.bat"：

- 入口文件：["FunASR_pat.bat"](../FunASR_pat.bat)
- 启动后地址：
  - API："http://localhost:8000"
  - Pat WebUI："http://localhost:7861"
- 启动方式：当前为单窗口托管模式；关闭该启动窗口时，会自动结束 API/UI 子进程
- 日志查看：API/UI 输出会写入根目录日志文件，并同步在启动窗口与前端 `"服务与调试"` 页展示

## 分开启动

- API：["run_api.bat"](../run_api.bat)
- Pat WebUI：["run_ui_pat.bat"](../run_ui_pat.bat)

## 模型下载与离线

### 自动下载（默认行为）

- API 启动后不预加载模型；首次请求对应能力时才会按需加载
- 模型统一存入**本机共享缓存**（ModelScope / HuggingFace 全局目录），查找顺序见 ["requirements.md"](./requirements.md)
- 语音模型默认走 ModelScope 源；翻译模型（TranslateGemma 含 GGUF）本地未命中时自动下载到 HF 全局缓存

### 手动下载（离线准备）

- 语音模型：运行 `scripts\prefetch_models.py`（默认预取 sensevoice / paraformer / fun-asr-nano / qwen3-asr）
- 单模型：运行 `scripts\download_model.py`（SenseVoiceSmall）
- 也可直接把模型放入本机共享缓存（见 requirements.md 的查找顺序）或 `workspace\models` 兜底目录

### 加载环境（GPU 优先，自动回退 CPU）

- 语音/情感/说话人模型：FunASR AutoModel，device 取启动参数（默认 cuda）
- NLLB 翻译：fp16 加载 GPU，失败回退 CPU
- TranslateGemma GGUF：llama.cpp 推理，显存不足时先卸载 FunASR 腾显存，仍不足回退 CPU

## 跑批测试（test 目录）

用途：遍历 "test\\" 下音视频文件，按模型分别输出 ASR 结果到 "test\\<模型名>\\"。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File ".\run_test_all_models.ps1"
```

## 常见排错点（便携包特有）

- torch CUDA DLL 找不到：需要把 "runtime/python/Lib/site-packages/torch/lib" 加入 "PATH"
  - 已在 "run_api.bat"/"run_ui_pat.bat" 里做了追加
- Python/依赖不匹配：直接用 "runtime/python/python.exe" 检查依赖是否完整
- 端口占用：确认 8000/7861 未被其他程序占用
