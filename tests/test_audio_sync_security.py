"""
程序说明：
验证思维导图使用无脚本、无外部 CDN 的安全 HTML 渲染，节点标题不能注入标签。
"""

from __future__ import annotations

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
    def test_markmap_escapes_untrusted_titles_without_scripts_or_cdn(self):
        output = get_markmap_html(
            '{"title":"<img src=x onerror=alert(1)>","children":[{"title":"</script><script>alert(2)</script>"}]}'
        )
        self.assertNotIn("<script", output.lower())
        self.assertNotIn("<iframe", output.lower())
        self.assertNotIn("cdn.jsdelivr", output.lower())
        self.assertIn("&lt;img", output)
        self.assertIn("&lt;/script&gt;", output)

    def test_invalid_json_returns_safe_message(self):
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


if __name__ == "__main__":
    unittest.main()
