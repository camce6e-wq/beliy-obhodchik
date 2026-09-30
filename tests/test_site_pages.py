import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

_root = Path(__file__).resolve().parent
while not (_root / "index.html").exists() and _root != _root.parent:
    _root = _root.parent
ROOT = _root
PAGES = [
    "index.html",
    "service-full.html",
    "service-dpi.html",
    "service-claude.html",
    "service-transfer.html",
    "order.html",
    "webapp/index.html",
]

VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(
                f"line {self.getpos()[0]}: closing </{tag}> with nothing open"
            )
            return
        top, pos = self.stack.pop()
        if top != tag:
            self.errors.append(
                f"line {self.getpos()[0]}: </{tag}> closes <{top}> opened at line {pos[0]}"
            )


def page_errors(path: Path):
    p = Checker()
    with open(path, encoding="utf-8") as f:
        p.feed(f.read())
    p.close()
    for tag, pos in p.stack:
        p.errors.append(f"line {pos[0]}: <{tag}> never closed")
    return p.errors


def page_ids(path: Path):
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r'id="([^"]+)"', text))


def internal_links(text: str):
    hrefs = re.findall(r'href="([^"]+)"', text)
    return [
        h
        for h in hrefs
        if not re.match(r"^(https?:|mailto:|tel:)//", h)
        and not h.startswith("#")
        and not h.startswith("//")
    ]


def main():
    errors = []
    checked_links = 0
    for name in PAGES:
        path = ROOT / name
        if not path.exists():
            errors.append(f"{name}: file missing")
            continue
        errors += [f"{name}: {e}" for e in page_errors(path)]
        html = path.read_text(encoding="utf-8")
        if html.count("<h1") != 1:
            errors.append(f"{name}: expected exactly one <h1>, got {html.count('<h1')}")
        if not re.search(r'<html[^>]*lang="ru"', html):
            errors.append(f"{name}: html[lang] is not 'ru'")
        if not re.search(r'<meta name="description" content="[^"]+', html):
            errors.append(f"{name}: missing non-empty meta description")
        title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        if title and not (10 <= len(title.group(1).strip()) <= 70):
            errors.append(
                f"{name}: title length {len(title.group(1).strip())} outside 10..70"
            )
        desc = re.search(r'<meta name="description" content="([^"]*)"', html)
        if desc and not (50 <= len(desc.group(1)) <= 165):
            errors.append(
                f"{name}: description length {len(desc.group(1))} outside 50..165"
            )
        if 'rel="canonical"' not in html:
            errors.append(f"{name}: missing canonical")
        if 'property="og:title"' not in html:
            errors.append(f"{name}: missing og:title")
        if 'property="og:image"' not in html:
            errors.append(f"{name}: missing og:image")
        if "<title>" not in html or html.count("<title>") != 1:
            errors.append(f"{name}: expected exactly one <title>")
        if html.count('class="skip-link"') == 0:
            errors.append(f"{name}: missing skip-link")
        navs = re.findall(r"<nav\b[^>]*>", html)
        if any("aria-label=" not in n for n in navs):
            errors.append(f"{name}: <nav> missing aria-label")
        for img in re.findall(r"<img\b[^>]*>", html):
            m = re.search(r'alt="([^"]*)"', img)
            if m is None or not m.group(1).strip():
                errors.append(f"{name}: <img> without alt text")
        for a in re.findall(r"<a\b[^>]*>.*?</a>", html, re.DOTALL):
            if not re.sub(r"<[^>]+>|\s+", "", a):
                errors.append(f"{name}: <a> with empty text")
        dup = [
            i for i, n in Counter(re.findall(r'id="([^"]+)"', html)).items() if n > 1
        ]
        if dup:
            errors.append("{}: duplicate ids: {}".format(name, ", ".join(dup)))
        ids = page_ids(path)
        for href in internal_links(html):
            checked_links += 1
            target, _, anchor = href.partition("#")
            target = target.split("?", 1)[0]
            if target:
                ref_path = path.parent / target
                if not ref_path.exists():
                    errors.append(f"{name}: broken link {href}")
                    continue
                if anchor and anchor not in page_ids(ref_path):
                    errors.append(f"{name}: broken anchor {anchor} (not in {target})")
            elif anchor and anchor not in ids:
                errors.append(f"{name}: missing anchor id=#{anchor}")

    if errors:
        print(f"SITE ERRORS ({len(errors)}):")
        for e in errors[:40]:
            print("  " + e)
        sys.exit(1)
    print(f"OK: {len(PAGES)} pages, {checked_links} internal links and anchors valid")


if __name__ == "__main__":
    main()
