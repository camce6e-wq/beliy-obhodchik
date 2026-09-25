import sys
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.tags = 0

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.tags += 1
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


p = Checker()
with open("index.html", encoding="utf-8") as f:
    p.feed(f.read())
p.close()

if p.stack:
    for tag, pos in p.stack:
        p.errors.append("line %d: <%s> never closed" % (pos[0], tag))

print("tags parsed: %d" % p.tags)
if p.errors:
    print("STRUCTURE ERRORS (%d):" % len(p.errors))
    for e in p.errors[:20]:
        print("  " + e)
    sys.exit(1)
print("HTML structure OK: all tags balanced")
