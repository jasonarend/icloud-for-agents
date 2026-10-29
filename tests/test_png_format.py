"""Validate complete bounded PNG structure and decompression before native use."""
import struct,unittest,zlib
from icloud_for_agents.png_format import dimensions,MAX_BYTES,MAX_DIMENSION

def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
def png(width=2,height=2,*,channels=3,depth=8,interlace=0,body=None,extra=b'',tail=b''):
 raw=body if body is not None else b''.join(b'\0'+b'\x35'*(width*channels)for _ in range(height))
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,depth,2 if channels==3 else 6,0,0,interlace))+extra+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')+tail
class PNGFormatTests(unittest.TestCase):
 def test_rgb_rgba_and_valid_scanline_filters(self):
  for channels in(3,4):
   for value in range(5):self.assertEqual(dimensions(png(channels=channels,body=(bytes([value])+b'\x55'*(2*channels))*2)),(2,2))
 def test_maximum_dimensions_use_a_bounded_stream(self):
  self.assertEqual(dimensions(png(MAX_DIMENSION,MAX_DIMENSION,channels=4)),(MAX_DIMENSION,MAX_DIMENSION))
 def test_invalid_type_header_size_crc_truncation_or_tail_refuses(self):
  valid=png()
  for raw in(None,'png',bytearray(valid),b'',b'not an image',valid[:-1],valid+b'\0',valid[:30]+bytes([valid[30]^1])+valid[31:],b'\x89PNG\r\n\x1a\n'+b'\xff'*4+valid[12:],valid+b'x'*MAX_BYTES):
   with self.assertRaises(ValueError):dimensions(raw)
 def test_zero_large_depth_interlace_and_nonrgb_headers_refuse(self):
  for raw in(png(0,2),png(2,0),png(MAX_DIMENSION+1,1),png(1,MAX_DIMENSION+1),png(depth=16),png(interlace=1)):
   with self.assertRaises(ValueError):dimensions(raw)
  raw=png();header=struct.pack('>IIBBBBB',2,2,8,3,0,0,0);bad=raw[:8]+chunk(b'IHDR',header)+raw[33:]
  with self.assertRaises(ValueError):dimensions(bad)
 def test_metadata_animation_and_duplicate_headers_refuse(self):
  header=struct.pack('>IIBBBBB',2,2,8,2,0,0,0)
  for extra in(chunk(b'tEXt',b'Synthetic metadata'),chunk(b'acTL',b'\0'*8),chunk(b'IHDR',header)):
   with self.assertRaises(ValueError):dimensions(png(extra=extra))
 def test_image_data_must_match_dimensions_and_filter_bounds(self):
  for body in(b'',b'\0',b'\0'*1000000,(b'\5'+b'\x55'*6)*2):
   with self.assertRaises(ValueError):dimensions(png(body=body))
 def test_multiple_streams_missing_checksum_and_nonfinal_end_refuse(self):
  signature=b'\x89PNG\r\n\x1a\n';header=chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0));data=zlib.compress(b'\0\x10\x20\x30')
  for value in(data+data,data[:-1],b'not zlib'):
   with self.assertRaises(ValueError):dimensions(signature+header+chunk(b'IDAT',value)+chunk(b'IEND',b''))
  with self.assertRaises(ValueError):dimensions(signature+header+chunk(b'IEND',b'')+chunk(b'IDAT',data))
 def test_many_chunks_and_empty_data_refuse(self):
  raw=png();header=raw[:33];body=zlib.compress(b'\0'+b'\x35'*6+b'\0'+b'\x35'*6)
  with self.assertRaises(ValueError):dimensions(header+b''.join(chunk(b'IDAT',bytes([v]))for v in body for _ in range(4))+chunk(b'IEND',b''))
  with self.assertRaises(ValueError):dimensions(header+chunk(b'IDAT',b'')+chunk(b'IEND',b''))
 def test_contiguous_split_image_data_is_valid(self):
  raw=png();header=raw[:33];body=zlib.compress(b'\0'+b'\x35'*6+b'\0'+b'\x35'*6);split=len(body)//2
  self.assertEqual(dimensions(header+chunk(b'IDAT',body[:split])+chunk(b'IDAT',body[split:])+chunk(b'IEND',b'')),(2,2))
