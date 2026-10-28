"""Read-only semantic projection of Notes HTML, including native table exports.

Does not execute HTML or fetch links/assets. Unknown objects remain explicitly
incomplete; this is not a full native Notes document or lossless export format.
"""
from html.parser import HTMLParser


class Projection(HTMLParser):
    blocks={'div','p','br','li','tr','th','td','h1','h2','h3','h4','h5','h6','blockquote','pre','ul','ol','table'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.tables=[];self.stack=[];self.objects=[];self.unknown_objects=0
        self.links=[];self.link_stack=[];self.ignored=0
    def boundary(self):
        self.parts.append(' ')
        if self.stack and self.stack[-1]['cell'] is not None:self.stack[-1]['cell'].append(' ')
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','head'}:self.ignored+=1;return
        if self.ignored:return
        if tag in self.blocks:self.boundary()
        if tag=='object':
            if len(self.objects)>=64:raise ValueError('Object nesting limit')
            self.objects.append(False)
        if tag in {'img','audio','video','iframe','embed'}:self.unknown_objects+=1
        if tag=='table':
            if len(self.stack)>=32:raise ValueError('Table nesting limit')
            for index in range(len(self.objects)):self.objects[index]=True
            self.stack.append({'rows':[],'cell':None})
        if tag=='tr' and self.stack:self.stack[-1]['rows'].append([])
        if tag in {'td','th'} and self.stack:
            if not self.stack[-1]['rows']:self.stack[-1]['rows'].append([])
            self.stack[-1]['cell']=[]
        if tag=='a':
            if len(self.link_stack)>=64:raise ValueError('Link nesting limit')
            href=dict(attrs).get('href')
            self.link_stack.append({'href':href,'parts':[]})
    def handle_endtag(self,tag):
        if tag in {'script','style','head'}:
            self.ignored=max(0,self.ignored-1);return
        if self.ignored:return
        if tag in {'td','th'} and self.stack:
            table=self.stack[-1]
            if table['cell'] is not None:
                table['rows'][-1].append(' '.join(''.join(table['cell']).split()));table['cell']=None
        if tag=='table' and self.stack:self.tables.append(self.stack.pop()['rows'])
        if tag=='object' and self.objects:
            if not self.objects.pop():self.unknown_objects+=1
        if tag=='a' and self.link_stack:
            link=self.link_stack.pop()
            if link['href']:self.links.append({'href':link['href'],'text':' '.join(''.join(link['parts']).split())})
        if tag in self.blocks:self.boundary()
    def handle_data(self,data):
        if self.ignored:return
        self.parts.append(data)
        for table in self.stack:
            if table['cell'] is not None:table['cell'].append(data)
        for link in self.link_stack:link['parts'].append(data)


def project_html(html):
    if not isinstance(html,str) or len(html)>200000:raise ValueError('Bounded HTML required')
    parser=Projection();parser.feed(html);parser.close()
    unknown=parser.unknown_objects+sum(not o for o in parser.objects)
    return {'text':' '.join(''.join(parser.parts).split()),'tables':parser.tables,'links':parser.links,
            'links_projection_complete':not (parser.link_stack or parser.ignored),
            'unknown_objects':unknown,'text_projection_complete':not (unknown or parser.stack or parser.ignored)}
