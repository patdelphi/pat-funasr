# -*- coding: utf-8 -*-
"""
音字联动前端 JS 模块
提供 Gradio HTML 组件所需的 JS/CSS，实现点击文字跳转音频 + 播放时高亮当前文字
"""


def get_audio_sync_html() -> str:
    """
    返回音字联动的完整 HTML+CSS+JS 模板
    由 Gradio gr.HTML() 渲染
    """
    return r"""
<!-- 音字联动容器 -->
<div id="audio-sync-container">
  <!-- 音频播放器 -->
  <audio id="audio-player" controls style="width:100%; margin-bottom:10px;"></audio>

  <!-- 转写文本容器(由 JS 动态填充) -->
  <div id="transcript-box" style="
    max-height: 400px;
    overflow-y: auto;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 12px;
    line-height: 1.8;
    font-size: 14px;
    background: #fafafa;
  ">
    <span style="color:#999;">等待转写结果...</span>
  </div>
</div>

<style>
  /* 高亮当前播放词 */
  .transcript-word.word-active {
    background: #4B3FE322 !important;
    color: #4B3FE3 !important;
    font-weight: 600;
    border-radius: 3px;
    padding: 1px 2px;
    transition: background 0.2s;
  }
  /* 可点击的词 */
  .transcript-word {
    cursor: pointer;
    border-radius: 2px;
    padding: 1px 1px;
    transition: background 0.15s;
  }
  .transcript-word:hover {
    background: #e8e8ff;
  }
  /* 说话人标签 */
  .speaker-label {
    display: inline-block;
    background: #4B3FE3;
    color: white;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 12px;
    margin: 4px 8px 4px 0;
    font-weight: 500;
  }
  /* 段落分隔 */
  .transcript-segment {
    margin-bottom: 8px;
    padding-bottom: 8px;
    border-bottom: 1px dashed #e0e0e0;
  }
  .transcript-segment:last-child {
    border-bottom: none;
  }
</style>

<script>
(function() {
  // 音频播放器
  const audio = document.getElementById('audio-player');
  const transcriptBox = document.getElementById('transcript-box');
  // 当前激活的元素
  let activeWord = null;

  // 高亮当前播放时间的词
  function highlightWordAtTime(currentTime) {
    const words = transcriptBox.querySelectorAll('.transcript-word');
    if (!words.length) return;

    let found = null;
    for (const word of words) {
      const start = parseFloat(word.dataset.start || 0);
      const end = parseFloat(word.dataset.end || 0);
      if (start <= currentTime && currentTime <= end) {
        found = word;
        break;
      }
    }

    // 没有精确匹配时，找最近的
    if (!found) {
      for (const word of words) {
        const start = parseFloat(word.dataset.start || 0);
        if (start <= currentTime) {
          found = word;
        } else {
          break;
        }
      }
    }

    if (found && found !== activeWord) {
      if (activeWord) activeWord.classList.remove('word-active');
      found.classList.add('word-active');
      activeWord = found;
      // 平滑滚动到当前词
      found.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  // 点击词跳转音频
  function seekAudio(startTime) {
    if (audio) {
      audio.currentTime = startTime;
      audio.play().catch(() => {});
    }
  }

  // 绑定点击事件
  function bindWordClicks() {
    const words = transcriptBox.querySelectorAll('.transcript-word');
    words.forEach(word => {
      word.addEventListener('click', function() {
        const start = parseFloat(this.dataset.start || 0);
        seekAudio(start);
      });
    });
  }

  // 音频时间更新事件
  if (audio) {
    audio.addEventListener('timeupdate', function() {
      highlightWordAtTime(audio.currentTime);
    });
    audio.addEventListener('seeked', function() {
      highlightWordAtTime(audio.currentTime);
    });
  }

  // 暴露给 Gradio Python 调用的全局函数
  window.__audioSync = {
    // 设置音频源
    setAudioSrc: function(url) {
      if (audio) {
        audio.src = url;
        audio.load();
      }
    },
    // 渲染转写结果
    renderTranscript: function(segments) {
      if (!transcriptBox || !segments) return;
      transcriptBox.innerHTML = '';
      segments.forEach(function(seg) {
        const segDiv = document.createElement('div');
        segDiv.className = 'transcript-segment';

        // 说话人标签
        if (seg.speaker) {
          const label = document.createElement('span');
          label.className = 'speaker-label';
          label.textContent = seg.speaker;
          segDiv.appendChild(label);
        }

        // 时间戳
        const timeSpan = document.createElement('span');
        timeSpan.style.cssText = 'color:#999; font-size:11px; margin-right:6px;';
        timeSpan.textContent = formatTime(seg.start) + ' ';
        segDiv.appendChild(timeSpan);

        // 词级时间戳
        if (seg.words && seg.words.length > 0) {
          seg.words.forEach(function(w) {
            const wordSpan = document.createElement('span');
            wordSpan.className = 'transcript-word';
            wordSpan.dataset.start = w.start !== undefined ? w.start : seg.start;
            wordSpan.dataset.end = w.end !== undefined ? w.end : seg.end;
            wordSpan.textContent = w.word || w.text || '';
            segDiv.appendChild(wordSpan);
            segDiv.appendChild(document.createTextNode(' '));
          });
        } else {
          // 无词级时间戳，整段作为一个可点击词
          const wordSpan = document.createElement('span');
          wordSpan.className = 'transcript-word';
          wordSpan.dataset.start = seg.start;
          wordSpan.dataset.end = seg.end;
          wordSpan.textContent = seg.text || '';
          segDiv.appendChild(wordSpan);
        }

        transcriptBox.appendChild(segDiv);
      });
      bindWordClicks();
    }
  };

  // 时间格式化
  function formatTime(seconds) {
    const mm = Math.floor(seconds / 60);
    const ss = Math.floor(seconds % 60);
    return mm + ':' + ss.toString().padStart(2, '0');
  }
})();
</script>
"""


def get_markmap_html(markmap_json: str) -> str:
    """
    返回思维导图渲染 HTML（markmap CDN + iframe srcdoc 方案）

    markmap_json: JSON 字符串，结构为 {title, children:[{title, children}]}

    设计说明：
    1. Gradio 6.x 的 gr.HTML 组件不会执行内联 <script> 标签（innerHTML
       设置的 script 不执行，这是浏览器规范行为），因此用 <iframe srcdoc>
       方案，iframe 内是独立文档上下文，<script> 可正常执行。
    2. markmap-view 与 markmap-lib 共享同一个 UMD 全局变量 `markmap`，
       其中 markmap.Markmap 为渲染器，markmap.Transformer 为 Markdown
       解析器。注意：不存在 markmapLib 全局变量。
    3. 不设置 sandbox 属性：经实测，sandbox（即使含 allow-scripts）
       会阻止 srcdoc iframe 加载外部 CDN 脚本，导致 markmap 为 undefined。
    4. 加 CDN 加载失败兜底：离线或 CDN 不可达时，回退为纯 HTML 树状列表，
       保证用户始终能看到思维导图内容。
    """
    # 防止 markmap_json 中的 </script> 提前结束 script 标签
    safe_json = markmap_json.replace("</script>", "<\\/script>")

    srcdoc_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ margin:0; padding:0; overflow:hidden; font-family:sans-serif; }}
  #markmap {{ width:100vw; height:100vh; }}
  #fallback {{ display:none; padding:16px; font-size:14px; line-height:1.8; }}
  #fallback .mm-node {{ margin:4px 0 4px 16px; padding:2px 6px; border-left:2px solid #4B3FE3; }}
  #fallback .mm-h1 {{ font-size:16px; font-weight:700; color:#4B3FE3; }}
  #fallback .mm-h2 {{ font-size:14px; font-weight:600; color:#333; }}
  #fallback .mm-h3 {{ font-size:13px; color:#555; }}
  #fallback .mm-h4 {{ font-size:12px; color:#777; }}
</style>
</head>
<body>
<div id="markmap"></div>
<div id="fallback"></div>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-view@0.18"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-lib@0.18"></script>
<script>
(function() {{
  var data = {safe_json};

  // 树状结构转 Markdown（markmap 输入格式）
  // 递归实现：按节点深度生成对应级别的 markdown 标题（# ~ ######，最多 6 级）
  // 避免硬编码层级导致深层节点丢失
  function toMarkdown(node, depth) {{
    if (!node) return '';
    depth = depth || 1;
    var prefix = '';
    for (var i = 0; i < depth && i < 6; i++) prefix += '#';
    var md = prefix + ' ' + (node.title || (depth === 1 ? 'Root' : '')) + '\\n';
    if (node.children && node.children.length) {{
      node.children.forEach(function(child) {{
        md += toMarkdown(child, depth + 1);
      }});
    }}
    return md;
  }}

  // 兜底：纯 HTML 树状渲染（CDN 失败时启用）
  function renderFallback(node, level) {{
    if (!node) return '';
    var cls = ['mm-h1','mm-h2','mm-h3','mm-h4'][Math.min(level, 3)];
    var html = '<div class="mm-node ' + cls + '">' + (node.title || '') + '</div>';
    if (node.children) {{
      node.children.forEach(function(child) {{
        html += renderFallback(child, level + 1);
      }});
    }}
    return html;
  }}

  try {{
    // markmap-view 与 markmap-lib 共享 `markmap` 全局对象
    if (typeof markmap === 'undefined' || !markmap.Transformer || !markmap.Markmap) {{
      throw new Error('markmap CDN 未加载');
    }}
    var md = toMarkdown(data);
    var Transformer = markmap.Transformer;
    var Markmap = markmap.Markmap;
    var transformer = new Transformer();
    var transformed = transformer.transform(md);
    var root = transformed.root;
    Markmap.create('#markmap', {{ autoFit: true }}, root);
  }} catch (e) {{
    // CDN 加载失败或渲染异常，回退为纯 HTML 树状列表
    var mm = document.getElementById('markmap');
    if (mm) mm.style.display = 'none';
    var fb = document.getElementById('fallback');
    if (fb) {{
      fb.style.display = 'block';
      fb.innerHTML = '<div style="color:#b00; margin-bottom:8px; font-size:12px;">' +
        '⚠ markmap CDN 加载失败，以下为降级树状视图：' + (e.message || '') + '</div>' +
        renderFallback(data, 0);
    }}
  }}
}})();
</script>
</body>
</html>"""

    # 对 srcdoc 内容做 HTML 实体转义，确保能安全嵌在属性值中
    srcdoc_escaped = (
        srcdoc_content
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    # 注意：不设 sandbox，否则 srcdoc iframe 无法加载外部 CDN 脚本
    return f"""<!-- markmap 思维导图（iframe srcdoc 方案，解决 Gradio 6.x 不执行内联 script 的问题） -->
<iframe srcdoc="{srcdoc_escaped}" style="width:100%; height:560px; border:1px solid #e0e0e0; border-radius:8px;"></iframe>"""


# 安全实现覆盖旧版 CDN/iframe 渲染入口。保留上方实现仅用于兼容历史差异审阅，运行时不再调用。
def get_markmap_html(markmap_json: str) -> str:
    """使用纯 HTML 树渲染思维导图，不执行脚本、不加载外部 CDN。"""
    import html
    import json

    try:
        payload = json.loads(markmap_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return "<div class='pat-mindmap-error'>思维导图数据无效，无法渲染。</div>"
    if not isinstance(payload, dict):
        return "<div class='pat-mindmap-error'>思维导图数据无效，无法渲染。</div>"

    node_count = [0]

    def render_node(node, depth: int) -> str:
        if not isinstance(node, dict) or depth > 8 or node_count[0] >= 500:
            return ""
        node_count[0] += 1
        title = html.escape(str(node.get("title") or "未命名节点"), quote=True)
        children = list(node.get("children") or []) if isinstance(node.get("children") or [], list) else []
        child_html = "".join(render_node(child, depth + 1) for child in children)
        nested = f"<ul>{child_html}</ul>" if child_html else ""
        return f"<li><span class='pat-mindmap-node level-{min(depth, 4)}'>{title}</span>{nested}</li>"

    tree = render_node(payload, 0)
    return f"""
<style>
  .pat-mindmap-safe {{ overflow:auto; max-height:560px; padding:18px; border:1px solid #e0e0e0; border-radius:8px; }}
  .pat-mindmap-safe ul {{ list-style:none; margin:6px 0 6px 20px; padding-left:14px; border-left:2px solid #d8d5ff; }}
  .pat-mindmap-safe > ul {{ margin-left:0; padding-left:0; border-left:0; }}
  .pat-mindmap-safe li {{ margin:7px 0; }}
  .pat-mindmap-node {{ display:inline-block; padding:5px 9px; border-radius:7px; background:#f4f2ff; color:#333; }}
  .pat-mindmap-node.level-0 {{ background:#4B3FE3; color:white; font-weight:700; }}
  .pat-mindmap-node.level-1 {{ color:#4B3FE3; font-weight:600; }}
</style>
<div class="pat-mindmap-safe" role="tree" aria-label="思维导图"><ul>{tree}</ul></div>
""".strip()
