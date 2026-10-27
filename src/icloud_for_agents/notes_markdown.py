"""Bounded, read-only Markdown projection. Never a native document backup.

HTML is parsed as data. No asset fetching, browser rendering or active content.
"""
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from .notes_content import project_html

VOID={'br','hr','img','input','meta','link','embed','source','wbr','area','base','col','param','track'}
HIDDEN={'script','style','head'}
OBJECTS={'img','audio','video','iframe','embed','canvas','svg','math','input'}

class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.root=['#document',{},[]];self.stack=[self.root];self.nodes=0;self.losses=set()
    def handle_starttag(self,tag,attrs):
        self.nodes+=1
        if self.nodes>20000 or len(self.stack)>=64:raise ValueError('Markdown projection complexity limit')
        node=[tag,dict(attrs),[]]
        if len(node[1])!=len(attrs):self.losses.add('malformed_html')
        self.stack[-1][2].append(node)
        if tag not in VOID:self.stack.append(node)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        if tag in VOID:return
        matches=[i for i,n in enumerate(self.stack) if i>0 and n[0]==tag]
        if not matches:self.losses.add('malformed_html');return
        at=matches[-1]
        if at!=len(self.stack)-1:self.losses.add('malformed_html')
        self.stack=self.stack[:at]
    def handle_data(self,text):self.stack[-1][2].append(text)
    def handle_comment(self,text):self.losses.add('non_content_markup_omitted')
    def handle_decl(self,text):self.losses.add('non_content_markup_omitted')
    def handle_pi(self,text):self.losses.add('non_content_markup_omitted')

def escape(text):return re.sub(r'([\\`*_{}\[\]<>()#+.!|~>=-])',r'\\\1',text)
def plain(node):
    if isinstance(node,str):return node
    if node[0] in HIDDEN:return ''
    text=''.join(plain(child) for child in node[2])
    return text+'\n' if node[0] in {'br','div','p','li','tr','td','th','table'} else text
def contains(node,tag):return not isinstance(node,str) and (node[0]==tag or any(contains(c,tag) for c in node[2]))
def link_allowed(href):
    if not isinstance(href,str) or not href or any(ord(c)<33 for c in href) or any(c in href for c in '<>\\'):return False
    try:
        url=urlsplit(href)
        if url.scheme not in {'http','https','applenotes','x-apple-reminderkit'} or url.username or url.password:return False
        if url.scheme in {'http','https'}:return bool(url.hostname) and url.port!=0
        return bool(url.path or url.netloc)
    except ValueError:return False

class Renderer:
    def __init__(self,losses):self.losses=losses
    def children(self,node):return ''.join(self.render(c) for c in node[2])
    def table(self,node):
        rows=[]
        def collect(n):
            if isinstance(n,str):return
            if n is not node and n[0]=='table':self.losses.add('nested_table_layout');return
            if n[1]:self.losses.add('visual_attributes_not_represented')
            if n[0]=='tr':rows.append(n);return
            for child in n[2]:collect(child)
        collect(node)
        if len(rows)>1000:self.losses.add('table_truncated');rows=rows[:1000]
        output=[];header=False
        for row in rows:
            cells=[c for c in row[2] if not isinstance(c,str) and c[0] in {'td','th'}]
            if len(cells)>100:self.losses.add('table_truncated');cells=cells[:100]
            values=[]
            for cell in cells:
                if set(cell[1])-{'colspan','rowspan'}:self.losses.add('visual_attributes_not_represented')
                if any(cell[1].get(k) not in (None,'1') for k in ('colspan','rowspan')):self.losses.add('table_merges')
                if contains(cell,'table'):
                    self.losses.add('nested_table_layout');value=escape(plain(cell))
                else:value=self.children(cell)
                value=re.sub(r'(?<!\\)\|',r'\\|',value)
                values.append(' '.join(value.split()))
            if not output:header=any(c[0]=='th' for c in cells)
            output.append(values)
        if not output:return ''
        width=max(len(row) for row in output)
        if not width:return ''
        output=[row+['']*(width-len(row)) for row in output]
        if not header:output.insert(0,['']*width)
        output.insert(1,['---']*width)
        return '\n\n'+'\n'.join('| '+' | '.join(row)+' |' for row in output)+'\n\n'
    def render(self,node):
        if isinstance(node,str):return escape(node)
        tag,attrs,children=node
        permitted={'href'} if tag=='a' else {'start'} if tag=='ol' else {'colspan','rowspan'} if tag in {'td','th'} else set()
        if set(attrs)-permitted:self.losses.add('visual_attributes_not_represented')
        if tag in HIDDEN:self.losses.add('non_content_markup_omitted');return ''
        if tag in OBJECTS:
            self.losses.add('native_objects_not_represented');return '\n[Native object omitted]\n'
        if tag=='object' and not contains(node,'table'):
            self.losses.add('native_objects_not_represented');return '\n[Native object omitted]\n'+self.children(node)
        if tag=='table':return self.table(node)
        if tag=='pre':
            text=plain(node);runs=[len(x) for x in re.findall(r'`+',text)];fence='`'*max([3]+[v+1 for v in runs]);return '\n\n'+fence+'\n'+text.rstrip('\n')+'\n'+fence+'\n\n'
        if tag=='code':
            text=plain(node);fence='`'*max([1]+[len(x)+1 for x in re.findall(r'`+',text)]);return fence+' '+text.replace('\n',' ')+' '+fence
        if tag in {'ul','ol'}:
            start=1
            if tag=='ol' and 'start' in attrs:
                try:
                    start=int(attrs['start'])
                    if not 1<=start<=999999:raise ValueError()
                except (TypeError,ValueError):start=1;self.losses.add('list_numbering')
            result=[]
            for child in children:
                if isinstance(child,str):
                    if child.strip():result.append(escape(child.strip()))
                    continue
                if child[0]!='li':result.append(self.render(child));continue
                text=self.children(child).strip();lines=text.splitlines() or [''];prefix=str(start)+'. ' if tag=='ol' else '- ';start+=1
                result.append(prefix+lines[0]+''.join('\n'+' '*len(prefix)+line for line in lines[1:]))
            return '\n\n'+'\n'.join(result)+'\n\n'
        text=self.children(node)
        if tag in {'b','strong'}:return '**'+text+'**'
        if tag in {'em','i'}:return '*'+text+'*'
        if tag in {'s','strike','del'}:return '~~'+text+'~~'
        if tag=='a':
            if any(contains(child,'a') for child in children):
                self.losses.add('nested_link_structure');text=escape(plain(node))
            if link_allowed(attrs.get('href')):return '['+text+'](<'+attrs['href']+'>)'
            if attrs.get('href'):self.losses.add('unsupported_link_omitted')
            return text
        if tag in {'h1','h2','h3','h4','h5','h6'}:return '\n\n'+'#'*int(tag[1])+' '+text.strip()+'\n\n'
        if tag=='blockquote':return '\n\n'+'\n'.join('> '+line for line in text.strip().splitlines())+'\n\n'
        if tag in {'p','div'}:return text+'\n\n'
        if tag=='br':return '\n'
        if tag=='hr':return '\n\n---\n\n'
        if tag not in {'#document','html','body','li','tbody','thead','tfoot','tr','td','th'}:self.losses.add('unsupported_formatting')
        if tag=='object':self.losses.add('native_object_metadata_not_represented')
        return text

def project_markdown(html,*,source_truncated=False):
    semantic=project_html(html);parser=Tree();parser.feed(html);parser.close()
    if len(parser.stack)>1:parser.losses.add('malformed_html')
    if source_truncated:parser.losses.add('source_truncated')
    renderer=Renderer(parser.losses);text=renderer.render(parser.root).strip()
    raw=text.encode('utf-8')
    if len(raw)>100000:text=raw[:100000].decode('utf-8',errors='ignore');parser.losses.add('output_truncated')
    incomplete={'source_truncated','output_truncated','table_truncated','malformed_html'}
    return {'text':text,'source':'exported_notes_html','text_complete':bool(semantic['text_projection_complete'] and not parser.losses&incomplete),
            'formatting_complete':not parser.losses,'losses':sorted(parser.losses),'round_trip_safe':False,'native_fidelity':'unverified'}
