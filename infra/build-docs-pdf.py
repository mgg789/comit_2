"""Build the current Russian documentation book from tracked Markdown sources."""
import re
import textwrap
from html import escape
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Preformatted


def build(root: Path) -> None:
    """Render every documentation chapter, preserving code as wrapped literal text."""
    font_root = Path('/System/Library/Fonts/Supplemental')
    pdfmetrics.registerFont(TTFont('Book', str(font_root/'Arial.ttf')))
    pdfmetrics.registerFont(TTFont('BookBold', str(font_root/'Arial Bold.ttf')))
    pdfmetrics.registerFont(TTFont('Code', str(font_root/'Courier New.ttf')))
    pdfmetrics.registerFontFamily('Book',normal='Book',bold='BookBold',italic='Book',boldItalic='BookBold')
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName='Book'
        if hasattr(style, 'fontSize'): style.leading=max(style.fontSize*1.35,13)
    styles['Normal'].fontSize=9;styles['Normal'].leading=13
    styles.add(ParagraphStyle('CodeBook',fontName='Code',fontSize=7,leading=9,backColor=colors.HexColor('#f3f3f3'),borderPadding=5))
    chapters = [root/'README.md',root/'AGENTS.md', *sorted((root/'docs').rglob('*.md'))]
    story=[Paragraph('COMIT — книга документации',styles['Title']),Paragraph('Актуальный снимок исходных документов. 11 октября 2026.',styles['Normal']),Spacer(1,18)]
    for file in chapters:
        story.append(Paragraph(escape(str(file.relative_to(root))),styles['Normal']))
    for file in chapters:
        story.extend([PageBreak(),Paragraph(escape(str(file.relative_to(root))),styles['Heading1'])])
        code=False;buffer=[]
        def paragraph(lines):
            if not lines:return
            raw=' '.join(lines)
            raw=re.sub(r'<[^>]+>', '', raw)
            raw=re.sub(r'!?\[([^\]]*)\]\(([^)]+)\)', r'\1', raw)
            value=escape(raw)
            value=re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',value)
            story.append(Paragraph(value,styles['Normal']));story.append(Spacer(1,5))
        for line in file.read_text().splitlines()+['']:
            if line.startswith('```'):
                if not code:paragraph(buffer);buffer=[];code=True
                else:
                    wrapped='\n'.join(part for row in buffer for part in (textwrap.wrap(row,95,replace_whitespace=False,drop_whitespace=False) or ['']))
                    story.append(Preformatted(escape(wrapped),styles['CodeBook']));story.append(Spacer(1,7));buffer=[];code=False
                continue
            if code:buffer.append(line);continue
            if not line.strip():paragraph(buffer);buffer=[];continue
            if line.startswith('#'):
                paragraph(buffer);buffer=[];level=len(line)-len(line.lstrip('#'));story.append(Paragraph(escape(line.lstrip('# ').strip()),styles['Heading'+str(min(level,3))]));continue
            # Long Markdown table rows wrap as ordinary text to avoid clipped wide tables.
            buffer.append(line)
        paragraph(buffer)
    def footer(canvas,doc):
        canvas.setFont('Book',8);canvas.drawString(42,24,'COMIT · исходники документации в репозитории');canvas.drawRightString(A4[0]-42,24,str(doc.page))
    SimpleDocTemplate(str(root/'docs/COMIT-docs.pdf'),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=44,bottomMargin=44,title='COMIT — документация',author='COMIT').build(story,onFirstPage=footer,onLaterPages=footer)


if __name__=='__main__':
    build(Path(__file__).resolve().parent.parent)
