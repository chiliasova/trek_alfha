import os
import re
import json
import glob
import argparse


# Чтение файлов
def read_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise ImportError("Установите pdfplumber: pip install pdfplumber")
        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        return "\n".join(parts)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# Очистка
def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)                 # HTML
    text = re.sub(r"!\[.*?\]\(.*?\)", " ", text)         # md-картинки
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)      # md-ссылки
    text = re.sub(r"[#*_`>]+", " ", text)                # md-символы
    text = re.sub(r"[ \t]+", " ", text)                  # пробелы/табы
    text = re.sub(r"\n{3,}", "\n\n", text)               # переносы
    return text.strip()


# Разбиение на предложения
ABBREVIATIONS = {"т.е", "т.д", "т.п", "т.к", "напр", "г", "гг",
                 "руб", "тыс", "млн", "млрд", "др"}


def split_sentences(text: str) -> list[str]:
    protected = text
    for abbr in ABBREVIATIONS:
        protected = protected.replace(f"{abbr}.", f"{abbr}__DOT__")
    protected = re.sub(r"(\d)\.(\d)", r"\1__DOT__\2", protected)

    parts = re.split(r"(?<=[.!?])\s+|\n+", protected)
    return [p.replace("__DOT__", ".").strip() for p in parts if p.strip()]


# Сборка чанков с перекрытием
def build_chunks(sentences: list[str], max_chars: int = 450,
                 overlap_sentences: int = 1) -> list[str]:
    chunks, current, current_len = [], [], 0

    for sent in sentences:
        if len(sent) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            words, buf, buf_len = sent.split(), [], 0
            for w in words:
                if buf_len + len(w) + 1 > max_chars and buf:
                    chunks.append(" ".join(buf))
                    buf, buf_len = [], 0
                buf.append(w)
                buf_len += len(w) + 1
            if buf:
                chunks.append(" ".join(buf))
            continue

        if current_len + len(sent) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = current[-overlap_sentences:] if overlap_sentences > 0 else []
            current_len = sum(len(s) + 1 for s in current)

        current.append(sent)
        current_len += len(sent) + 1

    if current:
        chunks.append(" ".join(current))
    return chunks


# Автоопределение темы
TOPIC_KEYWORDS = {
    "taxes":     ["налог", "усн", "патент", "осно", "нпд", "фнс",
                  "декларац", "ндс", "ндфл"],
    "marketing": ["маркетинг", "реклам", "ozon", "wildberries", "трафик",
                  "воронк", "smm"],
    "sales":     ["продаж", "клиент", "crm", "лид", "b2b", "сделк",
                  "переговор"],
    "legal":     ["юр", "договор", "ип", "ооо", "регистрац", "лиценз"],
    "finance":   ["финанс", "бюджет", "выручк", "прибыл", "cashflow",
                  "касс"],
}


def guess_topic(text: str) -> str:
    low = text.lower()
    scores = {t: sum(low.count(kw) for kw in kws)
              for t, kws in TOPIC_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


# Основной пайплайн
def process_folder(raw_dir: str, meta_path: str, out_path: str):
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    files = sorted(glob.glob(os.path.join(raw_dir, "*")))
    total = 0

    with open(out_path, "w", encoding="utf-8") as out:
        for path in files:
            fname = os.path.basename(path)
            if fname not in meta:
                print(f"Пропускаю {fname} — нет записи в meta.json")
                continue

            cleaned = clean_text(read_file(path))
            sentences = split_sentences(cleaned)

            # Платный материал — только описание (первые ~15 предложений)
            if meta[fname].get("is_paid", False):
                sentences = sentences[:15]

            for i, chunk in enumerate(build_chunks(sentences)):
                if len(chunk) < 80:
                    continue
                record = {
                    "id": f"{fname}__chunk_{i:03d}",
                    "text": chunk,
                    "source_file": fname,
                    "title": meta[fname]["title"],
                    "url": meta[fname]["url"],
                    "topic": meta[fname].get("topic") or guess_topic(chunk),
                    "is_paid": meta[fname].get("is_paid", False),
                    "target_stage": meta[fname].get("target_stage", "any"),
                    "level": meta[fname].get("level", "any"),
                    "chunk_index": i,
                    "char_len": len(chunk),
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1

    print(f"Готово! {total} чанков записаны в {out_path}")


# Валидация
def validate_chunks(path: str):
    lens, empty, too_long = [], 0, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            lens.append(c["char_len"])
            if not c["text"].strip():
                empty += 1
            if c["char_len"] > 600:
                too_long += 1
    if not lens:
        print("Чанков нет - проверь входные файлы.")
        return
    print("\nВалидация чанков:")
    print(f"Всего: {len(lens)}")
    print(f"Средняя длина: {sum(lens) / len(lens):.0f} символов")
    print(f"Мин/Макс: {min(lens)} / {max(lens)}")
    print(f"Пустых: {empty} | Слишком длинных (>600): {too_long}")


# CLI
if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser()
    p.add_argument("--raw",  default=os.path.join(BASE_DIR, "data/raw"))
    p.add_argument("--meta", default=os.path.join(BASE_DIR, "meta.json"))
    p.add_argument("--out",  default=os.path.join(BASE_DIR, "output/chunks.jsonl"))
    args = p.parse_args()

    process_folder(args.raw, args.meta, args.out)
    validate_chunks(args.out)