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
    返回思维导图渲染 HTML（markmap CDN）
    markmap_json: JSON 字符串，结构为 {title, children:[{title, children}]}
    """
    return f"""
<!-- markmap 思维导图容器 -->
<div id="markmap-container" style="width:100%; height:400px; border:1px solid #e0e0e0; border-radius:8px;"></div>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-view"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-lib@0.18"></script>
<script>
(function() {{
  const data = {markmap_json};
  // 转换为 markmap 需要的 markdown tree 格式
  function toMarkdown(node) {{
    if (!node) return '';
    let md = '# ' + (node.title || 'Root') + '\\n';
    if (node.children) {{
      node.children.forEach(function(child, i) {{
        md += '## ' + (child.title || '') + '\\n';
        if (child.children) {{
          child.children.forEach(function(grandchild) {{
            md += '### ' + (grandchild.title || '') + '\\n';
          }});
        }}
      }});
    }}
    return md;
  }}
  const md = toMarkdown(data);
  const {{ Transformer }} = markmap;
  const {{ Markmap }} = markmap;
  const transformer = new Transformer();
  const {{ root }} = transformer.transform(md);
  Markmap.create('#markmap-container', {{}}, root);
}})();
</script>
"""
