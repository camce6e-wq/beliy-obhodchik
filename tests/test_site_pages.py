import re
import sys
from collections import Counter
from pathlib import Path
from html.parser import HTMLParser

_root = Path(__file__).resolve().parent
while not (_root / "index.html").exists() and _root != _root.parent:
    _root = _root.parent
ROOT = _root
PAGES = ["index.html", "service-full.html", "service-dpi.html",
         "service-claude.html", "service-transfer.html"]

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}


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
            self.errors.append("line %d: closing </%s> with nothing open" % (self.getpos()[0], tag))
            return
        top, pos = self.stack.pop()
        if top != tag:
            self.errors.append("line %d: </%s> closes <%s> opened at line %d"
                               % (self.getpos()[0], tag, top, pos[0]))


def page_errors(path: Path):
    p = Checker()
    with open(path, encoding="utf-8") as f:
        p.feed(f.read())
    p.close()
    for tag, pos in p.stack:
        p.errors.append("line %d: <%s> never closed" % (pos[0], tag))
    return p.errors


def page_ids(path: Path):
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r'id="([^"]+)"', text))


def internal_links(text: str):
    hrefs = re.findall(r'href="([^"]+)"', text)
    return [h for h in hrefs if not re.match(r'^(https?:|mailto:|tel:)//', h) and not h.startswith("#") and not h.startswith("//")]


def main():
    errors = []
    checked_links = 0
    for name in PAGES:
        path = ROOT / name
        if not path.exists():
            errors.append("%s: file missing" % name)
            continue
        errors += ["%s: %s" % (name, e) for e in page_errors(path)]
        html = path.read_text(encoding="utf-8")
        if html.count("<h1") != 1:
            errors.append("%s: expected exactly one <h1>, got %d" % (name, html.count("<h1")))
        if not re.search(r'<html[^>]*lang="ru"', html):
            errors.append("%s: html[lang] is not 'ru'" % name)
        if not re.search(r'<meta name="description" content="[^"]+', html):
            errors.append("%s: missing non-empty meta description" % name)
        if "<title>" not in html or html.count("<title>") != 1:
            errors.append("%s: expected exactly one <title>" % name)
        if html.count('class="skip-link"') == 0:
            errors.append("%s: missing skip-link" % name)
        navs = re.findall(r"<nav\b[^>]*>", html)
        if any("aria-label=" not in n for n in navs):
            errors.append("%s: <nav> missing aria-label" % name)
        for img in re.findall(r"<img\b[^>]*>", html):
            m = re.search(r'alt="([^"]*)"', img)
            if m is None or not m.group(1).strip():
                errors.append("%s: <img> without alt text" % name)
        for a in re.findall(r"<a\b[^>]*>.*?</a>", html, re.S):
            if not re.sub(r"<[^>]+>|\s+", "", a):
                errors.append("%s: <a> with empty text" % name)
        dup = [i for i, n in Counter(re.findall(r'id="([^"]+)"', html)).items() if n > 1]
        if dup:
            errors.append("%s: duplicate ids: %s" % (name, ", ".join(dup)))
        ids = page_ids(path)
        for href in internal_links(html):
            checked_links += 1
            target, _, anchor = href.partition("#")
            if target:
                if target.startswith("img/") or target.startswith("css/"):
                    asset = ROOT / target
                    if not asset.exists():
                        errors.append("%s: broken asset link %s" % (name, href))
                    continue
                ref_path = ROOT / target
                if not ref_path.exists():
                    errors.append("%s: broken page link %s" % (name, href))
                    continue
                if anchor and anchor not in page_ids(ref_path):
                    errors.append("%s: broken anchor %s (not in %s)" % (name, anchor, target))
            elif anchor and anchor not in ids:
                errors.append("%s: missing anchor id=#%s" % (name, anchor))

    if errors:
        print("SITE ERRORS (%d):" % len(errors))
        for e in errors[:40]:
            print("  " + e)
        sys.exit(1)
    print("OK: %d pages, %d internal links and anchors valid" % (len(PAGES), checked_links))


if __name__ == "__main__":
    main()