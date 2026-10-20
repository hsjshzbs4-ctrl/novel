"""对比样本与全书章节：句长、段长、对话占比、段落结构。

用法：python bench/style_metrics.py [章节目录]
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from novel.checks import count_words  # noqa: E402

SENTENCE_END = "。！？…"
QUOTE = "“"
DEFAULT_BOOK = Path(r"E:\deep seek-Harness\books\mo-shi-7")


def analyze(name: str, text: str) -> dict:
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paragraphs) < 5:
        paragraphs = [p.strip() for p in body.splitlines() if p.strip()]

    sentences: list[str] = []
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
        f"{row['name']:<10} 字{row['words']:>5}  段{row['paragraphs']:>4}  "
        f"段均{row['avg_para']:>5}  句均{row['avg_sentence']:>5}  "
        f"句/段{row['sent_per_para']:>4}  对话{row['dialogue_ratio']:>5}%  短段{row['short_para_ratio']:>5}%"
    )


def main() -> None:
    book = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BOOK
    rows = [analyze("番茄样本", (ROOT / ".tmp" / "fanqie_sample.txt").read_text(encoding="utf-8"))]

    for path in sorted((book / "chapters").glob("ch-*.md")):
        rows.append(analyze(path.stem, path.read_text(encoding="utf-8")))

    out = "\n".join(fmt(r) for r in rows)
    (ROOT / ".tmp" / "metrics.txt").write_text(out, encoding="utf-8")
    print("ok")


if __name__ == "__main__":
    main()
