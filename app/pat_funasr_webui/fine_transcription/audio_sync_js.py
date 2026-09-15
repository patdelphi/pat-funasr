# -*- coding: utf-8 -*-
"""
音字联动 + 思维导图前端渲染模块

统一用 iframe srcdoc 方案，解决 Gradio 6.x 的 gr.HTML 不执行内联
<script> 的问题（浏览器 innerHTML 设置的 script 标签不执行）。
同时用 CSS 变量 + prefers-color-scheme 自动适配暗色主题。

暴露 window.__audioSync API 给 Gradio Python 端做流式更新：
- 首次 get_audio_sync_html() 时 bake 完整数据
- 后续通过 setAudioSrc / renderTranscript 增量推送，不重建 iframe
"""

import json


def json_for_inline_script(value) -> str:
    """序列化内联脚本数据，防止不可信文本提前闭合 script 标签。"""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _srcdoc_escape(srcdoc_content: str) -> str:
    """srcdoc iframe 属性值转义：先做 script-safe 再做 HTML 属性转义。"""
    safe = srcdoc_content.replace("</script>", "<\\/script>")
    return (
        safe
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def get_audio_sync_html(audio_url: str = "", segments: list | None = None) -> str:
    """音字联动 iframe srcdoc。

    完整打包：音频播放器 + 转写文本（含词级时间戳）+ 点击跳转 + 播放高亮。
    全部放在 iframe srcdoc 里，解决 Gradio 6.x gr.HTML 不执行 script 的问题。
    同时暴露 window.__audioSync API，供 Python 端流式推送增量 segments。

    Args:
        audio_url: 音频文件路径（本地路径自动转 base64 data URI）或已托管 URL
        segments: 转写段列表，每个段含 start/end/text/words/speaker
    """
    import base64, mimetypes
    # 本地路径 → base64 data URI
    # 原因：Gradio 的 /gradio_api/file= 路由带 login_check + 文件白名单，
    # srcdoc iframe 无法可靠访问（curl 实测返回 403）。
    # data URI 在 srcdoc 里完全合法，跨域/登录都不涉及。
    resolved_url = audio_url or ""
    audio_too_large = False
    if resolved_url and not resolved_url.startswith(("http://", "https://", "data:", "/")):
        # 本地绝对路径 → 读文件 → base64
        try:
            import os as _os
            if _os.path.isfile(resolved_url):
                file_size = _os.path.getsize(resolved_url)
                # 限制：srcdoc 总大小浏览器通常允许几 MB，音频超过 30MB 就放弃
                if file_size <= 30 * 1024 * 1024:
                    with open(resolved_url, "rb") as f:
                        raw = f.read()
                    mime = mimetypes.guess_type(resolved_url)[0] or "audio/wav"
                    b64 = base64.b64encode(raw).decode("ascii")
                    resolved_url = f"data:{mime};base64,{b64}"
                else:
                    resolved_url = ""
                    audio_too_large = True
        except Exception:
            resolved_url = ""
    safe_segments = json_for_inline_script(segments or [])
    safe_audio = json_for_inline_script(resolved_url)

    # 音频过大时显示在播放器位置的警告条（JS 运行时判断 audioUrl 为空）
    large_audio_warning = (
        "⚠ 音频文件超过 30MB，音字联动已自动跳过播放功能。转写文本仍可浏览，"
        "如需听音定位请先压缩音频。"
    )

    srcdoc_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{
    --pat-as-bg: #ffffff;
    --pat-as-fg: #333333;
    --pat-as-border: #e0e0e0;
    --pat-as-box-bg: #fafafa;
    --pat-as-active-bg: #4B3FE322;
    --pat-as-active-fg: #4B3FE3;
    --pat-as-hover: #e8e8ff;
    --pat-as-primary: #4B3FE3;
    --pat-as-primary-fg: #ffffff;
    --pat-as-muted: #999999;
    --pat-as-seg-line: #e0e0e0;
    --pat-as-line: #d8d5ff;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --pat-as-bg: #1a1a2e;
      --pat-as-fg: #e8e8e8;
      --pat-as-border: #3a3a5c;
      --pat-as-box-bg: #16213e;
      --pat-as-active-bg: #4B3FE355;
      --pat-as-active-fg: #b4a8ff;
      --pat-as-hover: #2a2a4a;
      --pat-as-muted: #777799;
      --pat-as-seg-line: #3a3a5c;
      --pat-as-line: #6b5ce7;
    }}
  }}
  html, body {{ margin:0; padding:0; background:var(--pat-as-bg); color:var(--pat-as-fg); }}
  #audio-sync-container {{ padding:8px; }}
  #audio-player {{ width:100%; margin-bottom:10px; }}
  .audio-warning {{
    padding:8px 12px; margin-bottom:10px; background:#fff5f5; border:1px solid #f0c0c0;
    border-radius:6px; color:#b00; font-size:12px;
  }}
  #transcript-box {{
    max-height: 400px; overflow-y:auto; border:1px solid var(--pat-as-border);
    border-radius:8px; padding:12px; line-height:1.8; font-size:14px;
    background:var(--pat-as-box-bg); color:var(--pat-as-fg);
  }}
  .transcript-word {{ cursor:pointer; border-radius:2px; padding:1px 1px; transition:background 0.15s; }}
  .transcript-word:hover {{ background:var(--pat-as-hover); }}
  .transcript-word.word-active {{
    background:var(--pat-as-active-bg) !important;
    color:var(--pat-as-active-fg) !important;
    font-weight:600; border-radius:3px; padding:1px 2px; transition:background 0.2s;
  }}
  .speaker-label {{
    display:inline-block; background:var(--pat-as-primary); color:var(--pat-as-primary-fg);
    font-size:11px; padding:2px 8px; border-radius:12px; margin:4px 8px 4px 0; font-weight:500;
  }}
  .transcript-segment {{ margin-bottom:8px; padding-bottom:8px; border-bottom:1px dashed var(--pat-as-seg-line); }}
  .transcript-segment:last-child {{ border-bottom:none; }}
  .time-stamp {{ color:var(--pat-as-muted); font-size:11px; margin-right:6px; }}
  .empty-hint {{ color:var(--pat-as-muted); }}
</style>
</head>
<body>
<div id="audio-sync-container">
  <audio id="audio-player" controls style="display:none;"></audio>
  <div id="audio-warning" class="audio-warning" style="display:none;"></div>
  <div id="transcript-box"><span class="empty-hint">等待转写结果...</span></div>
</div>
<script>
(function() {{
  const audio = document.getElementById('audio-player');
  const box = document.getElementById('transcript-box');
  const warningEl = document.getElementById('audio-warning');
  let activeWord = null;
  // 当前已渲染的 segments 副本（供 seekTo 等使用）
  let currentSegments = [];

  // === 工具函数 ===

  // 时间格式化（秒 → MM:SS）
  function fmt(seconds) {{
    const s = Math.max(0, Math.floor(seconds || 0));
    const mm = Math.floor(s / 60);
    const ss = s % 60;
    return mm + ':' + (ss < 10 ? '0' : '') + ss;
  }}

  // 高亮当前播放时间对应的词
  function highlightWordAtTime(t) {{
    const words = box.querySelectorAll('.transcript-word');
    if (!words.length) return;
    let found = null;
    // 精确匹配：s <= t && t <= e
    for (const w of words) {{
      const s = parseFloat(w.dataset.start || 0);
      const e = parseFloat(w.dataset.end || 0);
      if (s <= t && t <= e) {{ found = w; break; }}
    }}
    // 兜底：找 start <= t 的最后一个
    if (!found) {{
      for (const w of words) {{
        const s = parseFloat(w.dataset.start || 0);
        if (s <= t) found = w; else break;
      }}
    }}
    if (found && found !== activeWord) {{
      if (activeWord) activeWord.classList.remove('word-active');
      found.classList.add('word-active');
      activeWord = found;
      found.scrollIntoView({{ behavior:'smooth', block:'center' }});
    }}
  }}

  // 点击词跳转（仅当音频可用时才 seek，否则静默）
  function seekTo(startTime) {{
    if (audio && audio.src) {{ audio.currentTime = startTime; audio.play().catch(() => {{}}); }}
  }}

  // 渲染转写（核心）：清空 box → 按 segments 重建 DOM
  function renderTranscript(segs) {{
    if (!box) return;
    activeWord = null;
    box.innerHTML = '';
    currentSegments = Array.isArray(segs) ? segs : [];
    if (!currentSegments.length) {{
      box.innerHTML = '<span class="empty-hint">等待转写结果...</span>';
      return;
    }}
    currentSegments.forEach(function(seg) {{
      const segDiv = document.createElement('div');
      segDiv.className = 'transcript-segment';

      if (seg.speaker) {{
        const label = document.createElement('span');
        label.className = 'speaker-label';
        label.textContent = seg.speaker;
        segDiv.appendChild(label);
      }}

      const timeSpan = document.createElement('span');
      timeSpan.className = 'time-stamp';
      timeSpan.textContent = fmt(seg.start || 0) + ' ';
      segDiv.appendChild(timeSpan);

      const words = seg.words && seg.words.length > 0 ? seg.words : null;
      if (words) {{
        words.forEach(function(w) {{
          const span = document.createElement('span');
          span.className = 'transcript-word';
          span.dataset.start = w.start !== undefined ? w.start : (seg.start || 0);
          span.dataset.end = w.end !== undefined ? w.end : (seg.end || 0);
          span.textContent = w.word || w.text || '';
          span.addEventListener('click', function() {{ seekTo(parseFloat(span.dataset.start)); }});
          segDiv.appendChild(span);
          segDiv.appendChild(document.createTextNode(' '));
        }});
      }} else {{
        const span = document.createElement('span');
        span.className = 'transcript-word';
        span.dataset.start = seg.start || 0;
        span.dataset.end = seg.end || 0;
        span.textContent = seg.text || '';
        span.addEventListener('click', function() {{ seekTo(parseFloat(span.dataset.start)); }});
        segDiv.appendChild(span);
      }}

      box.appendChild(segDiv);
    }});
  }}

  // 设置音频源 + 绑定事件监听
  function setAudioSrc(audioUrl) {{
    if (!audioUrl) {{
      audio.style.display = 'none';
      return;
    }}
    // 清理旧监听（先简单做法：重建 audio src，浏览器自动解绑旧事件）
    audio.src = audioUrl;
    audio.style.display = 'block';
    // 先移除再重绑，避免重复绑定
    audio.removeEventListener('timeupdate', audio._onTimeUpdate);
    audio.removeEventListener('seeked', audio._onSeeked);
    audio._onTimeUpdate = function() {{ highlightWordAtTime(audio.currentTime); }};
    audio._onSeeked = function() {{ highlightWordAtTime(audio.currentTime); }};
    audio.addEventListener('timeupdate', audio._onTimeUpdate);
    audio.addEventListener('seeked', audio._onSeeked);
  }}

  // === 初始化 ===
  const bakedAudioUrl = {safe_audio};
  const bakedSegments = {safe_segments};

  if (bakedAudioUrl) {{
    setAudioSrc(bakedAudioUrl);
  }} else if (warningEl && {json_for_inline_script(large_audio_warning)}) {{
    // audioUrl 为空时显示警告（Python 端 base64 失败了）
    warningEl.textContent = {json_for_inline_script(large_audio_warning)};
    warningEl.style.display = 'block';
  }}
  renderTranscript(bakedSegments);

  // === 暴露给 Gradio Python 端调用的全局 API ===
  window.__audioSync = {{
    setAudioSrc: setAudioSrc,
    renderTranscript: renderTranscript,
    seekTo: seekTo,
    highlightWordAtTime: highlightWordAtTime,
    getSegments: function() {{ return currentSegments; }}
  }};
  // 桥接到 parent：gradio_app.py 的 sync_script 在 parent 页面执行
  // iframe srcdoc 与 parent 同源（null origin），可互相访问
  try {{ if (parent && parent !== window) parent.__audioSync = window.__audioSync; }} catch(e) {{}}
}})();
</script>
</body>
</html>"""

    srcdoc_escaped = _srcdoc_escape(srcdoc_content)
    return f"""<!-- 音字联动 iframe srcdoc（解决 Gradio 6.x script 不执行问题） -->
<iframe srcdoc="{srcdoc_escaped}" style="width:100%; height:480px; border:1px solid #e0e0e0; border-radius:8px;"></iframe>"""


def get_markmap_html(markmap_json: str) -> str:
    """思维导图：把 LLM 返回的 JSON 转成 Markdown，然后用 markmap CDN 渲染。

    关键修复：
    1. 兼容多种数据格式：{title, children}（标准）、{meeting_title, sections}（summary 格式）、{title}（单节点）
    2. markmap CDN 加载失败时降级为纯 Markdown 列表（不依赖任何 JS）
    3. 全部放在 iframe srcdoc 里，解决 Gradio 6.x HTML 组件不执行 script 的问题
    4. CDN 加 3s 超时兜底，超时后保留 fallback 并显示提示
    """
    # 校验 JSON 有效性
    try:
        payload = json.loads(markmap_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        # 降级：纯 HTML 提示
        return (
            '<div style="padding:16px; border:1px dashed #e0e0e0; border-radius:8px; color:#999;">'
            '⚠ 思维导图数据无效</div>'
        )

    # ---- 数据格式归一化：转成 markmap 需要的 {title, children} ----
    _MAX_DEPTH = 15  # 防止循环引用或极深层嵌套导致 RecursionError

    def _normalize_node(node: dict, depth: int = 0) -> dict | None:
        """把各种可能的 key 名归一成 title/children。带深度保护。"""
        if depth > _MAX_DEPTH or not isinstance(node, dict):
            return None
        # 兼容各种 title 字段名
        title = (
            node.get("title")
            or node.get("topic")
            or node.get("meeting_title")
            or node.get("name")
            or node.get("label")
            or ""
        )
        # 兼容各种 children 字段名
        children_raw = (
            node.get("children")
            or node.get("sections")
            or node.get("items")
            or node.get("nodes")
            or []
        )
        if not isinstance(children_raw, list):
            children_raw = []
        children = []
        for c in children_raw:
            normed = _normalize_node(c, depth + 1)
            if normed:
                children.append(normed)
        return {"title": str(title).strip() or "(空)", "children": children}

    root = _normalize_node(payload)
    if not root or not root["children"]:
        root = root or {"title": "思维导图", "children": []}

    # 用 Markdown 表示（markmap 的 transformer 吃这个）
    def _to_markdown(node: dict, depth: int = 1) -> str:
        if depth > 6:
            return ""
        md = "#" * depth + " " + (node.get("title") or "(空)") + "\n"
        for child in node.get("children", []):
            md += _to_markdown(child, depth + 1)
        return md

    markdown_text = _to_markdown(root)

    # 纯 HTML fallback 树（不依赖任何外部 CDN，永远能显示）
    def _render_fallback(node: dict, level: int = 0) -> str:
        colors = ["#4B3FE3", "#6b5ce7", "#8b7ce7", "#a89ce7", "#c4bce7"]
        color = colors[min(level, len(colors) - 1)]
        title = node.get("title", "(空)")
        html = (
            f'<div style="margin:3px 0 3px {level*16}px; padding:4px 10px; '
            f'border-left:2px solid {color}; font-size:{"14" if level<2 else "13" if level<3 else "12"}px; '
            f'font-weight:{"600" if level<2 else "400"};">{title}</div>'
        )
        for child in node.get("children", []):
            html += _render_fallback(child, level + 1)
        return html

    fallback_html = _render_fallback(root)
    safe_md = json.dumps(markdown_text, ensure_ascii=False).replace("</script>", "<\\/script>")

    srcdoc_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{
    --pat-mm-bg: #ffffff;
    --pat-mm-fg: #333333;
    --pat-mm-border: #e0e0e0;
    --pat-mm-primary: #4B3FE3;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --pat-mm-bg: #1a1a2e;
      --pat-mm-fg: #e8e8e8;
      --pat-mm-border: #3a3a5c;
    }}
  }}
  html, body {{ margin:0; padding:0; background:var(--pat-mm-bg); color:var(--pat-mm-fg); }}
  #markmap {{ width:100%; height:400px; display:none; }}
  #fallback {{ padding:8px; }}
  .mm-toggle {{ padding:6px 12px; margin:8px; background:#f0f0ff; border:1px solid #e0e0ff;
    border-radius:6px; cursor:pointer; font-size:12px; color:var(--pat-mm-primary); }}
  .mm-toggle:hover {{ background:#e8e8ff; }}
  .mm-hint {{ padding:4px 8px; font-size:11px; color:#999; text-align:center; }}
</style>
</head>
<body>
<button id="toggle" class="mm-toggle" style="display:none;">切换视图</button>
<div id="markmap"></div>
<div id="fallback">{fallback_html}</div>
<div id="mm-hint" class="mm-hint" style="display:none;">
  ⚠ markmap CDN 加载超时，以上为降级树状视图
</div>
<script>
(function() {{
  var md = {safe_md};
  var mmEl = document.getElementById('markmap');
  var fbEl = document.getElementById('fallback');
  var toggleBtn = document.getElementById('toggle');
  var hintEl = document.getElementById('mm-hint');
  var cdnOk = false;
  var cdnDone = false;

  function tryMarkmap() {{
    if (typeof markmap === 'undefined') return false;
    try {{
      var transformer = new markmap.Transformer();
      var root = transformer.transform(md).root;
      mmEl.style.display = 'block';
      fbEl.style.display = 'none';
      hintEl.style.display = 'none';
      markmap.Markmap.create('#markmap', {{ autoFit: true }}, root);
      cdnOk = true;
      toggleBtn.style.display = 'none';
      return true;
    }} catch(e) {{
      return false;
    }}
  }}

  // 加载单个 CDN 脚本，带超时
  function loadScript(src, timeoutMs) {{
    timeoutMs = timeoutMs || 5000;
    return new Promise(function(resolve) {{
      var done = false;
      var s = document.createElement('script');
      s.src = src;
      function finish() {{
        if (done) return;
        done = true;
        resolve();
      }}
      s.onload = finish;
      s.onerror = finish;  // 失败也 resolve
      setTimeout(finish, timeoutMs);  // 超时也 resolve
      document.head.appendChild(s);
    }});
  }}

  function showFallbackWithHint() {{
    // CDN 整体加载失败或超时 → 显示 fallback + 提示
    fbEl.style.display = 'block';
    mmEl.style.display = 'none';
    hintEl.style.display = 'block';
    toggleBtn.style.display = 'block';
  }}

  // 并行加载 3 个 CDN，全部带超时
  Promise.all([
    loadScript('https://cdn.jsdelivr.net/npm/d3@7', 5000),
    loadScript('https://cdn.jsdelivr.net/npm/markmap-view@0.18', 5000),
    loadScript('https://cdn.jsdelivr.net/npm/markmap-lib@0.18', 5000)
  ]).then(function() {{
    // 给 markmap-view 一点时间初始化全局变量
    setTimeout(function() {{
      cdnDone = true;
      if (!tryMarkmap()) {{
        showFallbackWithHint();
      }}
    }}, 200);
  }});

  // 兜底：3 秒后如果 CDN 还没完成，强制显示 fallback
  setTimeout(function() {{
    if (!cdnDone) {{
      showFallbackWithHint();
    }}
  }}, 3000);

  // 手动切换视图
  window.toggleView = function() {{
    if (mmEl.style.display === 'none') {{
      // 切到 markmap 视图
      if (!cdnOk && !tryMarkmap()) {{
        // CDN 还没好，重试一次
        showFallbackWithHint();
        return;
      }}
      if (cdnOk) {{
        mmEl.style.display = 'block';
        fbEl.style.display = 'none';
        hintEl.style.display = 'none';
      }}
    }} else {{
      // 切回 fallback
      mmEl.style.display = 'none';
      fbEl.style.display = 'block';
      hintEl.style.display = cdnDone && !cdnOk ? 'block' : 'none';
    }}
  }};
}})();
</script>
</body>
</html>"""

    srcdoc_escaped = _srcdoc_escape(srcdoc_content)
    return (
        '<!-- 思维导图 iframe srcdoc -->\n'
        f'<iframe srcdoc="{srcdoc_escaped}" '
        'style="width:100%; height:560px; border:1px solid #e0e0e0; border-radius:8px;"></iframe>'
    )
