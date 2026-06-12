# -*- coding: utf-8 -*-
"""
程序说明：
Pat WebUI 跨语言翻译辅助模块。

包含：
- 网络请求调用后端 API
- 对 srt、vtt、tsv、json 等常见音视频转写输出格式的提取与回填翻译
- 长文本安全切分算法
- 文件翻译路由分发
"""

import json
import urllib.request
import urllib.parse
from pathlib import Path
import tempfile
import re

SUPPORTED_TRANSLATION_LANGUAGES = {
    "zho_Hans", "zho_Hant", "eng_Latn", "jpn_Jpan", "kor_Kore",
    "fra_Latn", "tha_Thai", "zsm_Latn", "vie_Latn"
}

DEFAULT_TRANSLATION_MODEL = "nllb-200-distilled-600m"


def request_translation(
    base_url: str,
    text: str | list[str],
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str | list[str]:
    """向后端 API 发送翻译请求。"""
    normalized_url = base_url.rstrip("/") + "/v1/translations"
    payload = {
        "text": text,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "model": model
    }
    # 填充高级生成参数
    for k, v in kwargs.items():
        if v is not None:
            payload[k] = v
    
    req_timeout = None if timeout <= 0 else timeout
    headers = {"Content-Type": "application/json"}
    
    try:
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            normalized_url,
            data=data_bytes,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=req_timeout) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            return resp_data["translated_text"]
    except Exception as exc:
        raise RuntimeError(f"翻译请求失败: {exc}")


def split_text_by_length(text: str, max_chars: int = 400) -> list[str]:
    """将大长文本按标点与长度切分为小句段，防止超出 NLLB 的输入限制。"""
    if not text:
        return []
    
    # 按照常见的句尾标点（中英文）、换行进行切分，但保留标点本身
    sentences = re.split(r"([。！？\n\r\.!\?]+)", text)
    
    chunks = []
    current_chunk = ""
    
    for i in range(0, len(sentences), 2):
        sentence = sentences[i]
        punctuation = sentences[i + 1] if i + 1 < len(sentences) else ""
        full_sentence = sentence + punctuation
        if not full_sentence:
            continue
            
        if len(current_chunk) + len(full_sentence) > max_chars:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = full_sentence
        else:
            current_chunk += full_sentence
            
    if current_chunk:
        chunks.append(current_chunk)
        
    return chunks


def translate_srt(
    base_url: str,
    content: str,
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """翻译 SRT 字幕文件内容，仅提取文字翻译并回填，保留时间戳与序号。"""
    # 按双换行切分 Block
    blocks = re.split(r"\n\s*\n", content.strip())
    translated_blocks = []
    
    # 提取所有 Block 的文本部分进行批量处理，减少 HTTP 请求开销
    text_list = []
    block_indices = []
    
    for idx, block in enumerate(blocks):
        lines = block.splitlines()
        if len(lines) >= 3:
            # 第三行及之后都是文字
            text = "\n".join(lines[2:])
            if text.strip():
                text_list.append(text)
                block_indices.append((idx, len(lines)))
                
    if not text_list:
        return content
        
    # 批量发送翻译
    translated_texts = request_translation(
        base_url, text_list, source_lang, target_lang, model, timeout, **kwargs
    )
    
    # 将翻译回填
    translated_map = {}
    for i, t in enumerate(translated_texts):
        block_idx = block_indices[i][0]
        translated_map[block_idx] = t
        
    for idx, block in enumerate(blocks):
        lines = block.splitlines()
        if len(lines) >= 3:
            if idx in translated_map:
                # 只保留前两行（序号和时间戳），把翻译的文本填回
                new_block_lines = lines[:2] + [translated_map[idx]]
                translated_blocks.append("\n".join(new_block_lines))
            else:
                translated_blocks.append(block)
        else:
            if block.strip():
                translated_blocks.append(block)
                
    return "\n\n".join(translated_blocks) + "\n"


def translate_vtt(
    base_url: str,
    content: str,
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """翻译 VTT 字幕文件内容，保留 WEBVTT 头和时间戳。"""
    # 找出 WEBVTT 头
    vtt_header = ""
    body_content = content
    header_match = re.match(r"^(WEBVTT[^\n]*\n+)", content)
    if header_match:
        vtt_header = header_match.group(1)
        body_content = content[len(vtt_header):]
        
    # 其余部分的 Block 提取回填逻辑和 SRT 几乎完全一致
    translated_body = translate_srt(
        base_url, body_content, source_lang, target_lang, model, timeout, **kwargs
    )
    return vtt_header + translated_body


def translate_tsv(
    base_url: str,
    content: str,
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """翻译 TSV 字幕内容，只翻译 text 列。"""
    lines = content.strip().splitlines()
    if not lines:
        return content
        
    headers = lines[0].split("\t")
    if "text" not in headers:
        # 如果没有 text 列，直接返回原文本
        return content
        
    text_idx = headers.index("text")
    translated_lines = [lines[0]]
    
    text_list = []
    line_indices = []
    
    for idx in range(1, len(lines)):
        parts = lines[idx].split("\t")
        if len(parts) > text_idx:
            val = parts[text_idx]
            if val.strip():
                text_list.append(val)
                line_indices.append(idx)
                
    if not text_list:
        return content
        
    # 批量发送翻译
    translated_texts = request_translation(
        base_url, text_list, source_lang, target_lang, model, timeout, **kwargs
    )
    
    translated_map = {}
    for i, t in enumerate(translated_texts):
        line_idx = line_indices[i]
        translated_map[line_idx] = t
        
    for idx in range(1, len(lines)):
        parts = lines[idx].split("\t")
        if idx in translated_map and len(parts) > text_idx:
            parts[text_idx] = translated_map[idx]
        translated_lines.append("\t".join(parts))
        
    return "\n".join(translated_lines) + "\n"


def translate_json(
    base_url: str,
    content: str,
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """翻译 JSON 格式转写结果，保留结构，翻译 text 以及 segments 中的 text 字段。"""
    try:
        data = json.loads(content)
    except Exception:
        # 若不是合法的 JSON，当成普通纯文本处理
        return content
        
    text_list = []
    paths = []  # 记录我们需要修改的字段路径
    
    if "text" in data and isinstance(data["text"], str):
        text_list.append(data["text"])
        paths.append(("text", None))
        
    if "segments" in data and isinstance(data["segments"], list):
        for idx, seg in enumerate(data["segments"]):
            if isinstance(seg, dict) and "text" in seg and isinstance(seg["text"], str):
                text_list.append(seg["text"])
                paths.append(("segments", idx))
                
    if not text_list:
        return content
        
    # 批量发送翻译
    translated_texts = request_translation(
        base_url, text_list, source_lang, target_lang, model, timeout, **kwargs
    )
    
    for i, t in enumerate(translated_texts):
        field, idx = paths[i]
        if field == "text":
            data["text"] = t
        elif field == "segments":
            data["segments"][idx]["text"] = t
            
    return json.dumps(data, ensure_ascii=False, indent=2)


def translate_text_preserving_paragraphs(
    base_url: str,
    text: str,
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """按段落（换行）切分，保留段落格式。对每个大段落如果过长则进一步切句，翻译后拼回。"""
    if not text:
        return ""
    
    # 用正则保留换行符切分段落
    parts = re.split(r"(\r?\n)", text)
    
    translated_parts = []
    # 提取所有实际的文本段落
    paragraphs_to_translate = []
    paragraph_indices = []
    
    for idx, part in enumerate(parts):
        # 如果不是换行符本身且含有文本
        if not re.match(r"^\r?\n$", part) and part.strip():
            paragraphs_to_translate.append(part)
            paragraph_indices.append(idx)
            
    if not paragraphs_to_translate:
        return text
        
    # 对每个大段落进行句级别进一步微切分
    final_translate_list = []
    mapping = []  # 记录每一段在翻译列表中的起始和截止下标
    
    for p_idx, para in enumerate(paragraphs_to_translate):
        para_chunks = split_text_by_length(para)
        start_idx = len(final_translate_list)
        final_translate_list.extend(para_chunks)
        end_idx = len(final_translate_list)
        mapping.append((p_idx, start_idx, end_idx))
        
    # 批量发送请求
    translated_all = request_translation(
        base_url, final_translate_list, source_lang, target_lang, model, timeout, **kwargs
    )
    
    if isinstance(translated_all, str):
        translated_all = [translated_all]
        
    # 拼回段落
    paragraph_translations = []
    for p_idx, start_idx, end_idx in mapping:
        para_trans_chunks = translated_all[start_idx:end_idx]
        paragraph_translations.append("".join(para_trans_chunks))
        
    # 组装回原样
    translated_map = {}
    for i, part_idx in enumerate(paragraph_indices):
        translated_map[part_idx] = paragraph_translations[i]
        
    for idx, part in enumerate(parts):
        if idx in translated_map:
            translated_parts.append(translated_map[idx])
        else:
            translated_parts.append(part)
            
    return "".join(translated_parts)


def translate_file(
    base_url: str,
    file_path: str,
    file_ext: str,
    source_lang: str,
    target_lang: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """读取文件并根据后缀路由到专门翻译器，将结果保存为临时文件并返回路径。"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"找不到要翻译的文件: {file_path}")
        
    content = p.read_text(encoding="utf-8", errors="replace")
    ext = file_ext.lower().strip()
    if not ext.startswith("."):
        ext = "." + ext
        
    if ext == ".srt":
        translated_content = translate_srt(base_url, content, source_lang, target_lang, model, timeout, **kwargs)
    elif ext == ".vtt":
        translated_content = translate_vtt(base_url, content, source_lang, target_lang, model, timeout, **kwargs)
    elif ext == ".tsv":
        translated_content = translate_tsv(base_url, content, source_lang, target_lang, model, timeout, **kwargs)
    elif ext in {".json", ".verbose_json"}:
        translated_content = translate_json(base_url, content, source_lang, target_lang, model, timeout, **kwargs)
    elif ext in {".txt", ".md"}:
        # 针对长纯文本采用高可靠性保留段落的分段器
        translated_content = translate_text_preserving_paragraphs(
            base_url, content, source_lang, target_lang, model, timeout, **kwargs
        )
    else:
        # 其他后缀直接按段落翻译
        translated_content = translate_text_preserving_paragraphs(
            base_url, content, source_lang, target_lang, model, timeout, **kwargs
        )
            
    # 写入临时文件，文件名包含源语言、目标语言和时间戳
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 语言代码取下划线前的简短部分（如 zho_Hans -> zho_Hans, eng_Latn -> eng_Latn）
    src_short = source_lang.split("_")[0] if "_" in source_lang else source_lang
    tgt_short = target_lang.split("_")[0] if "_" in target_lang else target_lang
    temp_dir = Path(tempfile.mkdtemp(prefix="pat-funasr-trans-"))
    out_file = temp_dir / f"{p.stem}_{src_short}_{tgt_short}_{timestamp}{ext}"
    out_file.write_text(translated_content, encoding="utf-8-sig")  # 默认加 BOM
    
    return str(out_file)


def convert_to_chinese_punctuation(text: str) -> str:
    """将文本中的英文半角标点符号替换为中文全角标点符号，避免破坏数字小数点、时间及URL。"""
    if not text:
        return ""
    
    # 1. 替换不需要上下文识别的简单符号
    text = text.replace(",", "，")
    text = text.replace("?", "？")
    text = text.replace("!", "！")
    text = text.replace(";", "；")
    text = text.replace("(", "（")
    text = text.replace(")", "）")
    
    # 2. 替换冒号，避开 URL (http://) 或时间 (12:30) 中的冒号
    # 若匹配到数字间的冒号或协议中的冒号则原样保留，单独的冒号替换为“：”
    text = re.sub(
        r"(\d:\d)|([a-zA-Z]+://)|(:)",
        lambda m: m.group(0) if (m.group(1) or m.group(2)) else "：",
        text
    )
    
    # 3. 替换英文句号，避开数字中的小数点 (3.14)
    # 若匹配到数字间的小数点则原样保留，单独的句号替换为“。”
    text = re.sub(
        r"(\d\.\d)|(\.)",
        lambda m: m.group(0) if m.group(1) else "。",
        text
    )
    
    # 4. 替换双引号，交替弯引号
    parts = text.split('"')
    new_parts = []
    for i, part in enumerate(parts):
        new_parts.append(part)
        if i < len(parts) - 1:
            if i % 2 == 0:
                new_parts.append("“")
            else:
                new_parts.append("”")
    text = "".join(new_parts)
    
    # 5. 去除全角标点后面紧跟的多余英文空格，优化中文排版
    text = re.sub(r"([，。？！：；])\s+", r"\1", text)
    
    return text
