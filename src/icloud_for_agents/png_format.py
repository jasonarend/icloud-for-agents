"""Bounded basic RGB/RGBA PNG validation; no metadata, animation or file paths."""
import struct
import zlib

MAX_BYTES=16384
MAX_DIMENSION=1024


def dimensions(raw):
    """Verify one complete 8-bit noninterlaced image and bounded scanline stream."""
    if not isinstance(raw,bytes) or not 57<=len(raw)<=MAX_BYTES or raw[:8]!=b'\x89PNG\r\n\x1a\n':
        raise ValueError('Use one PNG of at most 16384 bytes')
    offset=8;count=0;size=None;compressed=bytearray();finished=False
    while offset<len(raw):
        count+=1
        if count>32 or offset+12>len(raw):raise ValueError('PNG chunk bound exceeded or incomplete chunk')
        length=struct.unpack_from('>I',raw,offset)[0];kind=raw[offset+4:offset+8];end=offset+12+length
        if end>len(raw):raise ValueError('Incomplete PNG chunk')
        payload=raw[offset+8:offset+8+length]
        if zlib.crc32(kind+payload)!=struct.unpack_from('>I',raw,end-4)[0]:raise ValueError('PNG chunk checksum failed')
        if kind==b'IHDR':
            if count!=1 or length!=13:raise ValueError('PNG requires one leading image header')
            width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',payload)
            if (not 1<=width<=MAX_DIMENSION or not 1<=height<=MAX_DIMENSION or depth!=8 or color not in (2,6)
                    or compression!=0 or filtering!=0 or interlace!=0):raise ValueError('Only bounded 8-bit noninterlaced RGB/RGBA PNG is supported')
            size=(width,height,3 if color==2 else 4)
        elif kind==b'IDAT':
            if size is None or not length:raise ValueError('PNG needs a header before nonempty image data')
            compressed.extend(payload)
        elif kind==b'IEND':
            if length!=0 or not compressed or end!=len(raw):raise ValueError('PNG requires one final empty end chunk after image data')
            finished=True
        else:raise ValueError('PNG metadata, animation and other chunk types are unsupported')
        offset=end
    if not finished or size is None:raise ValueError('Incomplete PNG image')
    width,height,channels=size;stride=1+width*channels;expected=stride*height
    try:
        decoder=zlib.decompressobj();scanlines=decoder.decompress(compressed,expected+1)
        if len(scanlines)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('PNG image data length or compressed stream did not verify')
    except zlib.error:raise ValueError('Invalid PNG compressed image data') from None
    if any(scanlines[row*stride]>4 for row in range(height)):raise ValueError('Invalid PNG scanline filter')
    return width,height
