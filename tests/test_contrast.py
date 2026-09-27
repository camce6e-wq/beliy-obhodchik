#!/usr/bin/env python3
"""WCAG-контраст ключевых пар палитры сайта и Mini App.

Смысл: дизайн-токены лежат в :root (css/style.css) и в webapp/style.css.
Если их поменяют «на глаз», тест ловит провал AA до деплоя.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while not (ROOT / "index.html").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent

# (label, css-файл, токен, на чём лежит, минимальный коэффициент)
PAIRS = [
    ("body text / paper", "css/style.css", "--ink", "--paper", 4.5),
    ("eyebrow / paper", "css/style.css", "--muted", "--paper", 4.5),
    ("card text / card", "css/style.css", "--muted", "--card", 4.5),
    ("dark section text", "css/style.css", "--on-dark", "--ink", 4.5),
    ("dark section muted", "css/style.css", "--on-dark-muted", "--ink", 4.5),
    ("button label / lime", "css/style.css", "--ink", "--lime", 4.5),
    ("app text / bg", "webapp/style.css", "--ink", "--bg", 4.5),
    ("app muted / bg", "webapp/style.css", "--muted", "--bg", 4.5),
    ("app muted / surface", "webapp/style.css", "--muted", "--surface", 4.5),
    ("app tab label", "webapp/style.css", "--muted", "--surface", 4.5),
]


def tokens(css_text):
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", css_text))


def lum(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def ratio(a, b):
    hi, lo = max(lum(a), lum(b)), min(lum(a), lum(b))
    return (hi + .05) / (lo + .05)


def main():
    cache = {}
    errors = []
    for label, rel, fg, bg, need in PAIRS:
        if rel not in cache:
            path = ROOT / rel
            if not path.exists():
                errors.append("%s: %s missing" % (label, rel))
                cache[rel] = {}
            else:
                cache[rel] = tokens(path.read_text(encoding="utf-8"))
        t = cache[rel]
        if fg not in t or bg not in t:
            errors.append("%s: token %s/%s not found in %s" % (label, fg, bg, rel))
            continue
        r = ratio(t[fg], t[bg])
        if r < need:
            errors.append("%s: %.2f < %.1f (%s on %s)" % (label, r, need, t[fg], t[bg]))
        else:
            print("OK  %-22s %.2f >= %.1f  (%s / %s)" % (label, r, need, t[fg], t[bg]))
    if errors:
        print("CONTRAST ERRORS (%d):" % len(errors))
        for e in errors:
            print("  " + e)
        return 1
    print("OK: %d contrast pairs pass WCAG AA" % len(PAIRS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
