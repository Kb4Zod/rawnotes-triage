"""Note header (metadata) handling for rawnotes-triage. Pure functions, standard library only.

A note's header is the first run of `- key: value` bullets under the title, before the first `## ` heading and after
at most a few lines of subtitle (blank lines may separate bullets; any other line ends the run). Only the header carries metadata:
a `- status:` line further down, in a quote, a list or a merged section, is just text and is never read or edited.
Texts here use "\\n" line endings; callers convert CRLF files before and after (see eol_of / with_eol).
"""
import re

HEAD_LINE = re.compile(r"^- ([A-Za-z][\w-]*):([ \t]*)(.*)$")
SECTION = re.compile(r"^#{2,6}\s")
SHOWN_KEYS = ("date", "status", "type", "promoted", "due", "shipped")  # the page shows these itself
PROSE_MAX = 3  # at most this many non-blank lines (a subtitle, say) may sit between the title and the header


def eol_of(raw):
    """"\\r\\n" if the file is consistently CRLF, else "\\n" (mixed or lone-CR files are normalised to LF)."""
    crlf = raw.count("\r\n")
    return "\r\n" if crlf and crlf == raw.count("\n") and raw.count("\r") == crlf else "\n"


def with_eol(text, eol): return text if eol == "\n" else text.replace("\n", eol)


def to_lf(raw): return raw.replace("\r\n", "\n") if eol_of(raw) == "\r\n" else raw


def title_index(lines):
    for i, l in enumerate(lines):
        if l.strip(): return i if re.match(r"^#\s+\S", l) else None
    return None


def header_lines(lines):
    """Indexes of the header's `- key: value` lines (empty list if the note has none)."""
    i = (title_index(lines) + 1) if title_index(lines) is not None else 0
    prose = 0
    while i < len(lines) and not HEAD_LINE.match(lines[i]):
        if SECTION.match(lines[i]) or re.match(r"^#\s", lines[i]): return []  # a heading came first: no header
        prose += bool(lines[i].strip())
        if prose > PROSE_MAX: return []
        i += 1
    if i >= len(lines): return []
    out = []
    while i < len(lines):
        if HEAD_LINE.match(lines[i]): out.append(i)
        elif lines[i].strip(): break
        i += 1
    return out


def field(text, key):
    """The header value of `key` (first word only; "" if missing, blank or a <placeholder>)."""
    lines = text.split("\n")
    for i in header_lines(lines):
        m = HEAD_LINE.match(lines[i])
        if m.group(1) == key:
            v = m.group(3).split(None, 1)[0] if m.group(3).strip() else ""
            return "" if v.startswith("<") else v
    return ""


def set_field(text, key, value):
    """Set `key` in the header. A present value is replaced (anything after its first word is kept, e.g. a
    trailing comment); a blank or <placeholder> value is filled in; a missing key is added to the end of the
    header, or right under the title if there is no header yet. Nothing outside the header is touched."""
    lines = text.split("\n"); head = header_lines(lines)
    for i in head:
        m = HEAD_LINE.match(lines[i])
        if m.group(1) != key: continue
        gap, rest = m.group(2) or " ", m.group(3)
        first = rest.split(None, 1)[0] if rest.strip() else ""
        if first and not first.startswith("<"): rest = value + rest[len(first):]
        else: rest = value + (("  " + rest) if rest.strip() else "")
        lines[i] = f"- {key}:{gap}{rest}"
        return "\n".join(lines)
    new = f"- {key}: {value}"
    if head: lines.insert(head[-1] + 1, new)
    else:
        t = title_index(lines)
        if t is None: lines[0:0] = [new, ""]
        elif lines[t + 1:t + 2] == [""]: lines[t + 2:t + 2] = [new, ""]
        else: lines[t + 1:t + 1] = ["", new, ""]
    return "\n".join(lines)


def drop_field(text, key):
    lines = text.split("\n")
    for i in header_lines(lines):
        if HEAD_LINE.match(lines[i]).group(1) == key: del lines[i]; break
    return "\n".join(lines)


def title_of(text, fname):
    m = re.search(r"^#[ \t]+(.+)$", text, re.M)
    return m.group(1).strip() if m else fname


def append_outcome(text, line):
    if re.search(r"^## Outcome[ \t]*$", text, re.M): return text.rstrip("\n") + f"\n{line}\n"
    return text.rstrip("\n") + f"\n\n## Outcome\n\n{line}\n"


QUESTION_HEAD = re.compile(r"^##[ \t]+What.?s the question[^\n]*$", re.M | re.I)


def question_span(text):
    """(start, end) character offsets of the question section's body, or None. It runs to the next `## ` heading."""
    m = QUESTION_HEAD.search(text)
    if not m: return None
    nxt = re.compile(r"^## ", re.M).search(text, m.end())
    return m.end(), nxt.start() if nxt else len(text)


def question_of(text):
    """The whole question section (every paragraph, list and code block), trimmed."""
    s = question_span(text)
    return text[s[0]:s[1]].strip() if s else ""


def question_summary(text, limit=280):
    """First paragraph of the question, cut at `limit` characters: for list rows and search, never for the reader."""
    q = question_of(text)
    return re.split(r"\n\s*\n", q, 1)[0].strip()[:limit] if q else ""


def note_body(text):
    """The note minus what the page already shows: the title, the date/status/type/... header lines and the
    question section (which the reader prints in full on its own)."""
    s = question_span(text)
    if s:
        start = QUESTION_HEAD.search(text).start()
        text = text[:start] + text[s[1]:]
    lines = text.split("\n"); drop = set(header_lines(lines))
    drop = {i for i in drop if HEAD_LINE.match(lines[i]).group(1) in SHOWN_KEYS}
    t = title_index(lines)
    if t is not None: drop.add(t)
    kept = [l for i, l in enumerate(lines) if i not in drop]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
