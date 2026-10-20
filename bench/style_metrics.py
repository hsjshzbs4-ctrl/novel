"""对比样本与我们的章节：句长、段长、对话占比、段落结构。"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from novel.checks import count_words  # noqa: E402

SENTENCE_END = "。！？…"
QUOTE = "“"


def analyze(name: str, text: str) -> dict:
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    # 有些样本是一行一段，也兼容单换行
    if len(paragraphs) < 5:
        paragraphs = [p.strip() for p in body.splitlines() if p.strip()]

    sentences = []
    for para in paragraphs:
        buf = ""
        for char in para:
            buf += char
            if char in SENTENCE_END:
                if len(buf.strip()) > 1:
                    sentences.append(buf.strip())
                buf = ""
        if len(buf.strip()) > 1:
            sentences.append(buf.strip())

    words = count_words(body)
    dialogue = [p for p in paragraphs if p.startswith(QUOTE) or p.count(QUOTE) >= 2]

    return {
        "name": name,
        "words": words,
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "avg_para": round(words / max(len(paragraphs), 1), 1),
        "avg_sentence": round(words / max(len(sentences), 1), 1),
        "sent_per_para": round(len(sentences) / max(len(paragraphs), 1), 2),
        "dialogue_ratio": round(len(dialogue) / max(len(paragraphs), 1) * 100, 1),
        "short_para_ratio": round(
            len([p for p in paragraphs if count_words(p) <= 20]) / max(len(paragraphs), 1) * 100, 1
        ),
    }


def fmt(row: dict) -> str:
    return (
        f"{row['name']:<12} 字{row['words']:>5}  段{row['paragraphs']:>4}  句{row['sentences']:>4}  "
        f"段均{row['avg_para']:>5}字  句均{row['avg_sentence']:>5}字  "
        f"句/段{row['sent_per_para']:>4}  对话{row['dialogue_ratio']:>5}%  短段{row['short_para_ratio']:>5}%"
    )


sample = (ROOT / ".tmp" / "fanqie_sample.txt").read_text(encoding="utf-8")
ours = Path(r"E:\deep seek-Harness\books\mo-shi-7\chapters\ch-001.md").read_text(encoding="utf-8")

rows = [analyze("番茄样本", sample), analyze("我们 ch1", ours)]
out = "\n".join(fmt(r) for r in rows)
(ROOT / ".tmp" / "metrics.txt").write_text(out, encoding="utf-8")
print("ok")
