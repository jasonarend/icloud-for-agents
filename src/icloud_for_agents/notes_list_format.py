"""Convert a deliberately small, bounded Markdown list subset to safe Notes HTML."""
from html import escape
import re
from .core import SafetyError

LIST=re.compile(r'^( *)([-*+]|[1-9][0-9]{0,3}\.) (.+)$')
HEADING=re.compile(r'^(#{1,4}) (.+)$')

def _plain(value):
    # Inline Markdown, native checklists, raw HTML and escaped syntax are not
    # guessed or silently converted to plain text.
    if (not value.strip() or any((ord(c)<32 and c!='\t') or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in value)
            or any(c in value for c in '*_`~[]<>\\') or re.match(r'^(?:#{1,6} |[-+] |[0-9]+[.)] )',value)
            or re.fullmatch(r'[-=]{3,}',value.strip()) or value.endswith('  ')
            or re.search(r'&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);',value)):
        raise SafetyError('Only plain text is supported inside headings, paragraphs and list items')
    return escape(value)

def format_lists(markdown):
    if not isinstance(markdown,str):raise SafetyError('Markdown input must be text')
    try:data=markdown.encode('utf-8')
    except UnicodeError:raise SafetyError('Markdown input must be valid Unicode') from None
    if not 1<=len(data)<=80000:raise SafetyError('Markdown input must be 1..80000 UTF-8 bytes')
    lines=markdown.replace('\r\n','\n').replace('\r','\n').split('\n')
    if len(lines)>2000:raise SafetyError('Markdown input exceeds the line limit')
    blocks=[];stack=[];items=0
    for line in lines:
        if not line.strip():stack=[];continue
        if '\t' in line:raise SafetyError('Use spaces rather than tabs for Markdown indentation')
        match=LIST.fullmatch(line)
        if match:
            spaces,marker,text=match.groups();depth=len(spaces)//2
            if len(spaces)%2 or depth>7 or depth>len(stack):raise SafetyError('Use nested lists indented by two spaces per level')
            kind='ul' if marker in {'-','*','+'} else 'ol';number=None if kind=='ul' else int(marker[:-1])
            if depth<len(stack):
                stack=stack[:depth+1]
                if stack[depth]['kind']!=kind:stack=stack[:depth]
            if depth==len(stack):
                node={'kind':kind,'items':[]}
                if depth:
                    if not stack[-1]['items']:raise SafetyError('Nested list requires a parent item')
                    stack[-1]['items'][-1]['children'].append(node)
                else:blocks.append(node)
                stack.append(node)
            node=stack[-1]
            if number is not None and number!=len(node['items'])+1:raise SafetyError('Numbered lists must start at 1 and increase consecutively')
            node['items'].append({'text':_plain(text),'children':[]});items+=1
            if items>1000:raise SafetyError('Markdown list exceeds the item limit')
            continue
        if line.startswith(' '):raise SafetyError('Indented non-list content is not supported')
        stack=[];heading=HEADING.fullmatch(line)
        if heading:
            if re.search(r'\s#+\s*$',heading[2]):raise SafetyError('Closing Markdown heading markers are not supported')
            blocks.append({'html':'<h'+str(len(heading[1]))+'>'+_plain(heading[2])+'</h'+str(len(heading[1]))+'>'});continue
        if line.startswith(('#','>','- ','* ','+ ')) or re.match(r'^[0-9]+[.)] ',line) or re.fullmatch(r'[-=]{3,}',line):
            raise SafetyError('Unsupported Markdown structure')
        blocks.append({'html':'<p>'+_plain(line)+'</p>'})
    if not blocks:raise SafetyError('Markdown input has no content')
    def render(node):
        if 'html' in node:return node['html']
        return '<'+node['kind']+'>'+''.join('<li>'+item['text']+''.join(render(child) for child in item['children'])+'</li>' for item in node['items'])+'</'+node['kind']+'>'
    html=''.join(render(block) for block in blocks)
    if len(html.encode('utf-8'))>90000:raise SafetyError('Converted Notes HTML exceeds the input limit')
    return {'format':'html','body':html,'coverage':{'subset':'plain headings, paragraphs and nested unordered or consecutive numbered lists',
            'native_checklists':False,'native_objects':False,'round_trip_safe':False,
            'limitations':['Inline Markdown, links, tables, code blocks and checklists are refused.',
                           'Each non-list line becomes a separate paragraph; list formatting is HTML, not native checklist state.']}}
