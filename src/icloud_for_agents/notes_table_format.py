"""Format bounded plain cell data as HTML for a separately reviewed Notes change."""
from html import escape
from .core import SafetyError


def format_table(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=100:
        raise SafetyError('Supply 1..100 table rows')
    if not isinstance(rows[0],list) or not 1<=len(rows[0])<=20:
        raise SafetyError('Supply 1..20 cells per row')
    width=len(rows[0]); encoded_rows=[]; size=len('<table></table>')
    for row in rows:
        if not isinstance(row,list) or len(row)!=width:
            raise SafetyError('Every table row must have the same number of cells')
        cells=[];size+=len('<tr></tr>')
        for cell in row:
            if (not isinstance(cell,str) or len(cell)>4000
                    or any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in cell)):
                raise SafetyError('Table cells must be bounded single-line Unicode text')
            encoded=escape(cell);size+=len('<td></td>')+len(encoded.encode('utf-8'))
            if size>80000:raise SafetyError('Converted table exceeds the Notes body limit')
            cells.append('<td>'+encoded+'</td>')
        encoded_rows.append('<tr>'+''.join(cells)+'</tr>')
    body='<table>'+''.join(encoded_rows)+'</table>'
    return {'format':'html','body':body,'coverage':{'native_table_identity':False,'round_trip_safe':False,
            'limitations':['Plain text cells only; merged cells, styling and native table identity are not represented.',
                           'Notes rendering and synchronization may change formatting.']}}
