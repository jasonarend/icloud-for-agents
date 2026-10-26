"""Literal edits of supported exported text nodes without HTML reserialization."""
from html import escape
from html.parser import HTMLParser
import re
from .core import SafetyError
from .notes_content import project_html


def split_title(html,title):
    match=re.match(r'\A\s*<(div|p|h[1-4])>.*?</\1>',html,re.S)
    if not match or project_html(match.group())['text']!=title:
        raise SafetyError('A separate complete exported title paragraph is required; no inferred title/body boundary')
    return html[:match.end()],html[match.end():]


def replace_text(html,fields):
    if not isinstance(fields,dict) or set(fields)!={'find','replacement','expected_matches'}:
        raise SafetyError('Literal replacement requires find, replacement and expected_matches')
    needle,replacement,count=fields['find'],fields['replacement'],fields['expected_matches']
    if (not isinstance(needle,str) or not 1<=len(needle)<=2000 or any(ord(c)<32 for c in needle)
            or not isinstance(replacement,str) or len(replacement.encode())>20000 or '\x00' in replacement
            or type(count) is not int or not 1<=count<=100 or needle==replacement):
        raise SafetyError('Invalid bounded literal text replacement')
    # Offsets refer to the original source, preserving every untouched byte.
    # Entities and matches spanning formatting nodes are intentionally refused.
    offsets=[0]
    for match in re.finditer('\n',html):offsets.append(match.end())
    spans=[]
    class Nodes(HTMLParser):
        def handle_data(self,text):
            row,column=self.getpos();base=offsets[row-1]+column;start=0
            while True:
                index=text.find(needle,start)
                if index<0:break
                spans.append((base+index,base+index+len(needle)));start=index+len(needle)
    parser=Nodes(convert_charrefs=False);parser.feed(html);parser.close()
    visible=project_html(html)['text']
    if len(spans)!=count or visible.count(needle)!=count:
        raise SafetyError('Exact visible and text-node match counts must agree; entity or cross-format matches need a different edit route')
    rendered=escape(replacement).replace('\n','<br>')
    for start,end in reversed(spans):html=html[:start]+rendered+html[end:]
    return html


def equivalent_export(expected,actual,*,empty_paragraph_placeholder=False):
    """Only tolerate line endings Notes inserts between complete block elements.

    Inline whitespace, text, entities, tags, attributes and preformatted content
    stay exact. No DOM/text-only comparison can erase a formatting difference.
    """
    if not isinstance(actual,str):return False
    if expected.rstrip()==actual.rstrip():return True
    if re.search(r'<(?:pre|code)\b',expected+' '+actual,re.I):return False
    def normalized(value):
        return re.sub(r'(</(?:div|p|h[1-4])>)[\r\n]+(?=<(?:div|p|h[1-4])>)',r'\1',value.rstrip())
    wanted=normalized(expected)
    if empty_paragraph_placeholder:wanted=wanted.replace('<div></div>','<div><br></div>')
    return wanted==normalized(actual)
