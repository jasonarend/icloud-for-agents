"""Bounded read-only projection of native Notes checklist paragraphs.

The wire field layout is documented by apple_cloud_notes_parser's notestore.proto.
This independent decoder uses only the standard library. It never serializes or
writes native content and does not reconstruct attachments or a restorable note.
"""
import hashlib
import uuid
import zlib
from .core import SafetyError

MAX_COMPRESSED=1048576
MAX_DECOMPRESSED=4194304
MAX_UNITS=100000
MAX_ITEMS=1000


def invalid():raise SafetyError('Unsupported or incomplete native checklist representation; no inferred checkbox state')


def _varint(data,pos):
    value=0
    for index in range(10):
        if pos>=len(data):invalid()
        byte=data[pos];pos+=1
        if index==9 and byte>1:invalid()
        value|=(byte&127)<<(7*index)
        if byte<128:
            if index and value<1<<(7*index):invalid()
            return value,pos
    invalid()


def _fields(data,budget):
    values={};position=0
    while position<len(data):
        budget[0]-=1
        if budget[0]<0:invalid()
        key,position=_varint(data,position);number,wire=key>>3,key&7
        if not 1<=number<2**29:invalid()
        if wire==0:value,position=_varint(data,position)
        elif wire in (1,2,5):
            if wire==2:size,position=_varint(data,position)
            else:size=8 if wire==1 else 4
            if size>len(data)-position:invalid()
            value=data[position:position+size];position+=size
        else:invalid()
        values.setdefault(number,[]).append((wire,value))
    return values


def _one(fields,number,wire,*,required=True):
    rows=fields.get(number,[])
    if not rows and not required:return None
    if len(rows)!=1 or rows[0][0]!=wire:invalid()
    return rows[0][1]


def decode(compressed):
    if type(compressed)is not bytes or not 1<=len(compressed)<=MAX_COMPRESSED:invalid()
    try:
        inflater=zlib.decompressobj(31);data=inflater.decompress(compressed,MAX_DECOMPRESSED+1)
    except zlib.error:invalid()
    if len(data)>MAX_DECOMPRESSED or not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:invalid()
    budget=[50000];root=_fields(data,budget);document=_fields(_one(root,2,2),budget)
    # Only the locally observed format version is admitted; unknown versions
    # must be verified independently rather than decoded optimistically.
    version=_one(document,2,0)
    if version!=0:invalid()
    note=_fields(_one(document,3,2),budget)
    try:text=_one(note,2,2).decode('utf-8',errors='strict')
    except UnicodeError:invalid()
    if any(char in text for char in ('\r','\v','\f','\x1c','\x1d','\x1e','\x85','\u2028','\u2029')):invalid()
    encoded=text.encode('utf-16-le');units=len(encoded)//2
    if units>MAX_UNITS:invalid()
    boundaries={0:0};position=0
    for index,char in enumerate(text):position+=2 if ord(char)>65535 else 1;boundaries[position]=index+1
    # Every paragraph is identified by its UTF-16 interval. Native attribute
    # runs may split inline formatting, but must agree throughout each item.
    lines=[];start=0
    for line in text.splitlines(keepends=True):
        length=len(line.encode('utf-16-le'))//2;lines.append((start,start+length,line.rstrip('\n\r')));start+=length
    runs=note.get(5,[])
    if len(runs)>10000 or (units and not runs):invalid()
    positions=[];position=0
    for wire,raw in runs:
        if wire!=2:invalid()
        run=_fields(raw,budget);length=_one(run,1,0);end=position+length
        if length<1 or end>units or position not in boundaries or end not in boundaries:invalid()
        paragraph_raw=_one(run,2,2,required=False);style=None
        if paragraph_raw is not None:
            paragraph=_fields(paragraph_raw,budget);kind=_one(paragraph,1,0,required=False);check=_one(paragraph,5,2,required=False)
            if kind==103:
                if check is None:invalid()
                checklist=_fields(check,budget);native_id=_one(checklist,1,2);done=_one(checklist,2,0);indent=_one(paragraph,4,0,required=False)
                if len(native_id)!=16 or done not in (0,1) or (indent is not None and not 0<=indent<=32):invalid()
                style=(str(uuid.UUID(bytes=native_id)),bool(done),0 if indent is None else indent)
            elif check is not None:invalid()
        positions.append((position,end,style));position=end
    if position!=units:invalid()
    items=[];used=set();run_index=0
    for start,end,line in lines:
        while run_index<len(positions) and positions[run_index][1]<=start:run_index+=1
        index=run_index;styles=[]
        while index<len(positions) and positions[index][0]<end:
            styles.append(positions[index][2]);index+=1
        native={style for style in styles if style is not None}
        if not native:continue
        if len(native)!=1 or any(style is None for style in styles):invalid()
        identity,done,indent=next(iter(native))
        if identity in used or len(items)>=MAX_ITEMS:invalid()
        used.add(identity);items.append({'id':identity,'text':line,'checked':done,'indent':indent,'start_utf16':start,'end_utf16':end})
    return {'format_version':version,'text_utf16_units':units,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'items':items,'representation_complete':False,
            'coverage':'Direct checklist paragraph runs only; rich objects, soft-line semantics, cross-device state and native editing are not represented'}
