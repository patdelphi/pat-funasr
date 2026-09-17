"""
程序说明：
验证思维导图使用 iframe srcdoc（Gradio 6.x 兼容），
节点标题不能注入危险标签；音字联动 JSON 不能闭合 script。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_WEBUI_DIR = _ROOT / "app" / "pat_funasr_webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from fine_transcription.audio_sync_js import (  # noqa: E402
    get_markmap_html,
    json_for_inline_script,
)


class TestAudioSyncSecurity(unittest.TestCase):
    def test_markmap_escapes_untrusted_titles(self):
        """未受信任的 title 中的 HTML 标签需经 json.dumps 严格转义。"""
        output = get_markmap_html(
            '{"title":"<img src=x onerror=alert(1)>","children":[{"title":"</script><script>alert(2)</script>"}]}'
        )
        # 必须是 iframe srcdoc 方案
        self.assertIn("<iframe", output.lower())
        # markmap CDN 脚本正常加载
        self.assertIn("cdn.jsdelivr", output.lower())
        # 受控生成的 </script> 闭合标签已显式转义为 <\/script>
        self.assertIn("\\/script", output)
        # 未受信任的 title 内容仍正确保留在 data JSON 中
        self.assertIn("alert(2)", output)

    def test_invalid_json_returns_safe_placeholder(self):
        output = get_markmap_html("{invalid")
        self.assertIn("思维导图数据无效", output)

    def test_inline_json_cannot_close_script_element(self):
        output = json_for_inline_script(
            [{"text": "</script><script>alert(1)</script>&\u2028"}]
        )

        self.assertNotIn("</script", output.lower())
        self.assertNotIn("<", output)
        self.assertNotIn("&", output)
        self.assertIn("\\u003c/script\\u003e", output)
        self.assertIn("\\u2028", output)

    def test_markmap_fallback_escapes_node_titles(self):
        """CDN fallback 树中节点 title 需 html.escape，防止标签注入执行。"""
        payload = json.dumps(
            {
                "title": "根",
                "children": [{"title": "<img src=x onerror=alert(1)>", "children": []}],
            },
            ensure_ascii=False,
        )
        output = get_markmap_html(payload)
        # 节点 title 先 html.escape（&lt;）再经 srcdoc 属性转义（& → &amp;），
        # 最终应出现 &amp;lt;img；若未转义只会是 &lt;img，能被还原成真实标签
        self.assertIn("&amp;lt;img", output)
        # markmap data JSON 仍保留原文（srcdoc 属性转义为 &lt;img，与 fallback 的 &amp;lt;img 区分）
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", output)


if __name__ == "__main__":
    unittest.main()
