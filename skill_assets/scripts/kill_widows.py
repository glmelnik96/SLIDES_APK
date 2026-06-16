#!/usr/bin/env python3
"""
kill_widows.py — типографическая чистка русского текста.

Применяет правила Copy Editor agent к произвольной строке:
- Неразрывный пробел после коротких слов и между числом+единицей
- Замена -- → —
- Кавычки "..." → «...»
- Двойные пробелы → одинарные
- Trim

Usage:
    python3 kill_widows.py "текст для чистки"
    echo "текст" | python3 kill_widows.py
"""
import sys, re


SHORT_WORDS_DEFAULT = {
    "в", "на", "и", "но", "с", "к", "у", "за", "от", "до", "по", "из",
    "о", "об", "что", "как", "же", "ли", "бы", "или", "а", "для", "при", "без",
    "не", "ни", "то", "так", "вы", "мы", "он", "она", "оно", "они",
}

NBSP = "\u00a0"


def load_short_words(path=None):
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return {line.strip().lower() for line in f if line.strip()}
        except FileNotFoundError:
            pass
    return SHORT_WORDS_DEFAULT


def kill_widows(text, short_words=None):
    if short_words is None:
        short_words = SHORT_WORDS_DEFAULT

    # 1. Trim and collapse spaces
    text = re.sub(r"[ \t]+", " ", text).strip()

    # 2. Double dashes / spaces-dash-spaces → em dash
    text = re.sub(r"(?<!\w)-{2,}(?!\w)", "—", text)
    text = re.sub(r" - ", " — ", text)

    # 3. Russian quotes (basic): "word" → «word» (если оба рядом)
    def replace_quotes(m):
        return f"«{m.group(1)}»"

    text = re.sub(r'"([^"]+)"', replace_quotes, text)

    # 4. Non-breaking space after short words
    def add_nbsp_short(m):
        word = m.group(1)
        if word.lower() in short_words:
            return word + NBSP
        return m.group(0)

    text = re.sub(r"(\b\w+)\s+", add_nbsp_short, text)

    # 5. Non-breaking space between number and unit/word (5 GB → 5\u00a0GB, 30 минут → 30\u00a0минут, 30% → leave)
    text = re.sub(r"(\d+)\s+([A-Za-zА-Яа-я]+)", lambda m: m.group(1) + NBSP + m.group(2), text)

    # 6. No double spaces (after all transformations)
    text = re.sub(r"  +", " ", text)

    return text


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read()

    short = load_short_words("dictionaries/short-words-ru.txt")
    print(kill_widows(text, short))


if __name__ == "__main__":
    main()
