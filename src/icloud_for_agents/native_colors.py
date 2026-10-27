"""Explicit sRGB list colors; no display-name, profile or palette guessing."""
import math
import re
from .core import SafetyError
CHANNELS=('red','green','blue','alpha')

def validate_color(value):
    if isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',value):
        return dict(zip(CHANNELS,[int(value[i:i+2],16)/255 for i in (1,3,5)]+[1.0]))
    if (not isinstance(value,dict) or set(value)!=set(CHANNELS)
            or any(type(value[k]) not in (int,float) or not 0<=value[k]<=1 or not math.isfinite(value[k]) for k in CHANNELS)):
        raise SafetyError('Use #RRGGBB or an explicit sRGB red/green/blue/alpha object with finite channels from 0 to 1')
    if value['alpha']!=1:raise SafetyError('This EventKit route supports opaque list colors only; alpha must be 1')
    return {k:float(value[k]) for k in CHANNELS}

def colors_equal(actual,expected):
    try:
        a=validate_color(actual);b=validate_color(expected)
        return all(abs(a[k]-b[k])<=0.000001 for k in CHANNELS)
    except SafetyError:return False
