"""Bounded MIME/HTML text extraction; no browser, URL resolution or resource I/O."""
import re
from html.parser import HTMLParser

TEXT_LIMIT = 50000
PART_LIMIT = 256
DEPTH_LIMIT = 40
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
EXCLUDE = {'head','script','style','noscript','template','iframe','object','embed','svg','canvas','title'}


def invisible(style):
    declarations = {}
    for item in style.lower().split(';'):
        if ':' in item:
            key, value = item.split(':', 1)
            declarations[key.strip()] = re.sub(r'\s+', '', value).removesuffix('!important')
    return (declarations.get('display') == 'none'
            or declarations.get('visibility') in {'hidden','collapse'}
            or declarations.get('mso-hide') == 'all'
            or declarations.get('color') == 'transparent'
            or bool(re.fullmatch(r'0(?:\.0+)?', declarations.get('opacity','')))
            or bool(re.fullmatch(r'0(?:\.0+)?(?:px|em|rem|%)?', declarations.get('font-size',''))))



class Styles(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.rules = []

    def handle_starttag(self, tag, attrs):
        if tag == 'style': self.active = True

    def handle_endtag(self, tag):
        if tag == 'style': self.active = False

    def handle_data(self, data):
        if self.active: self.rules.append(data)


class VisibleText(HTMLParser):
    def __init__(self, hidden_selectors):
        super().__init__(convert_charrefs=True)
        self.hidden_selectors = hidden_selectors
        self.stack = []
        self.parts = []
        self.length = 0
        self.truncated = False

    def add(self, value):
        left = TEXT_LIMIT - self.length
        if len(value) > left: self.truncated = True
        self.parts.append(value[:left]); self.length += min(len(value), left)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tokens = {tag, '#' + attrs.get('id', '')}
        tokens.update('.' + value for value in attrs.get('class', '').split())
        hidden = (tag in EXCLUDE or 'hidden' in attrs or attrs.get('aria-hidden','').lower() == 'true'
                  or invisible(attrs.get('style','')) or bool(tokens & self.hidden_selectors)
                  or bool(self.stack and self.stack[-1][1]))
        if not hidden and tag in {'br','hr','p','div','li','tr','h1','h2','h3','h4','section','article'}:
            self.add('\n')
        if tag not in VOID:
            if len(self.stack) >= 256:
                self.truncated = True
                raise ValueError('HTML nesting bound')
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID: self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in {'p','div','li','tr','section','article'} and not (self.stack and self.stack[-1][1]):
            self.add('\n')

    def handle_data(self, data):
        if not (self.stack and self.stack[-1][1]): self.add(data)


def html_text(value):
    styles = Styles(); styles.feed(value)
    selectors = set()
    for names, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', '\n'.join(styles.rules)):
        if invisible(declarations):
            for selector in names.split(','):
                selector = selector.strip()
                if re.fullmatch(r'[.#]?[A-Za-z_][\w-]*', selector): selectors.add(selector)
    parser = VisibleText(selectors)
    try:
        parser.feed(value); parser.close()
    except (ValueError, RecursionError):
        parser.truncated = True
    text = ''.join(parser.parts)
    text = re.sub('[\u200b-\u200f\ufeff\u034f]', '', text)
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\n[ \t]*\n+', '\n\n', text).strip()
    return text, parser.truncated


def mime_text(msg):
    plain, rich = [], []
    defects, limited = bool(msg.defects), False
    visited = 0

    def walk(part, depth=0):
        nonlocal visited, defects, limited
        visited += 1
        if visited > PART_LIMIT or depth > DEPTH_LIMIT:
            limited = True; return
        defects = defects or bool(part.defects)
        # Entire attached/forwarded-message subtrees are excluded.
        if part.get_content_disposition() == 'attachment' or part.get_content_type() == 'message/rfc822': return
        if part.is_multipart():
            for child in part.iter_parts(): walk(child, depth+1)
            return
        kind = part.get_content_type()
        if kind not in {'text/plain','text/html'}: return
        try:
            payload = part.get_payload(decode=True) or b''
            defects = defects or bool(part.defects)
            try: value = payload.decode(part.get_content_charset() or 'utf-8', 'replace')
            except (LookupError, UnicodeError):
                value = payload.decode('utf-8','replace'); defects = True
        except (TypeError, ValueError):
            defects = True; return
        (plain if kind == 'text/plain' else rich).append(value)

    try: walk(msg)
    except (ValueError, TypeError, RecursionError): defects = True; limited = True
    usable_plain = [value for value in plain if value.strip()]
    if usable_plain:
        text = '\n'.join(usable_plain)
        limited = limited or len(text) > TEXT_LIMIT
        return {'body_text':text[:TEXT_LIMIT], 'body_format':'plain', 'html_only_or_no_plaintext':False,
                'body_extraction_limited':limited, 'mime_defects_detected':defects}
    extracted, html_limited = html_text('\n'.join(rich)) if rich else ('',False)
    return {'body_text':extracted, 'body_format':'html_to_text' if rich else 'none',
            'html_only_or_no_plaintext':True, 'body_extraction_limited':limited or html_limited,
            'mime_defects_detected':defects}
