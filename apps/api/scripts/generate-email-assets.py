"""Regenerate trusted email wordmark/digit rasters from the bundled Moon Walk font.

Run from the repository: python3 apps/api/scripts/generate-email-assets.py.
Requires Pillow only for this offline authoring step; API has no new dependency.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
r=Path(__file__).resolve().parents[3]
a=r/'apps/api/src/auth/email-assets'
f=r/'packages/ui-kit/src/design-assets/fonts/moon-walk.ttf'
scale=3
font=ImageFont.truetype(str(f),32*scale)
word=Image.new('RGBA',(160*scale,38*scale))
logo=Image.open(a/'logo.png').convert('RGBA');logo=logo.resize((38*scale,38*scale),Image.Resampling.LANCZOS)
word.alpha_composite(logo,(0,0))
draw=ImageDraw.Draw(word);box=draw.textbbox((0,0),'comit',font=font)
draw.text((50*scale,-box[1]+7*scale),'comit',font=font,fill='#f0f0f0')
word.save(a/'wordmark.png')
font=ImageFont.truetype(str(f),42*scale)
for char in '0123456789:':
    width=round(font.getlength(char))
    im=Image.new('RGBA',(width,51*scale));draw=ImageDraw.Draw(im);box=draw.textbbox((0,0),char,font=font)
    draw.text((0,(51*scale-(box[3]-box[1]))//2-box[1]),char,font=font,fill='#f0f0f0')
    im.save(a/f'glyph-{"colon" if char==":" else char}.png')
