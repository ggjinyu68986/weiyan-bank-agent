# -*- coding: utf-8 -*-
"""《技术文档.docx》生成器 v2.1：以 docs/技术文档.md 为唯一权威源，解析为 Word。
封面（无页码）→ 目录（TOC 域，无页码）→ 正文（页码从 1）。
样式：Title 黑体18 / H1 黑体16 / H2 黑体14 / H3 黑体12 / 正文宋体12 1.5倍行距首行缩进2字符 / 表格宋体10.5。
"""
import io
import docx
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

MD = r"D:\Desktop\weiyan-bank-agent\docs\技术文档.md"
OUT = r"D:\Desktop\weiyan-bank-agent\docs\技术文档.docx"

doc = Document()

# ---------- 页面设置 ----------
for sec in doc.sections:
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Cm(2.5)

# ---------- 样式 ----------
def set_font(style, cn, en, size, bold=False):
    style.font.name = en
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts'); rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), cn)
    for attr in ('w:asciiTheme', 'w:hAnsiTheme', 'w:eastAsiaTheme', 'w:cstheme'):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]

styles = doc.styles
set_font(styles['Normal'], '宋体', 'Times New Roman', 12)
styles['Normal'].paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
styles['Normal'].paragraph_format.first_line_indent = Pt(24)
styles['Normal'].paragraph_format.space_before = Pt(0)
styles['Normal'].paragraph_format.space_after = Pt(0)
set_font(styles['Title'], '黑体', 'Times New Roman', 18, True)
styles['Title'].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
styles['Title'].paragraph_format.space_after = Pt(18)
for lv, sz in ((1, 16), (2, 14), (3, 12)):
    s = styles['Heading %d' % lv]
    set_font(s, '黑体', 'Times New Roman', sz, True)
    s.paragraph_format.space_before = Pt(14 if lv == 1 else 11 if lv == 2 else 9)
    s.paragraph_format.space_after = Pt(6 if lv == 1 else 5 if lv == 2 else 4)
    s.paragraph_format.first_line_indent = Pt(0)
set_font(styles['Caption'], '宋体', 'Times New Roman', 10.5)
styles['Caption'].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
styles['Caption'].paragraph_format.space_before = Pt(4)

# ---------- 工具函数 ----------
def para(text='', style='Normal', align=None):
    p = doc.add_paragraph(style=style)
    if align: p.paragraph_format.alignment = align
    return p

def cn_quotes(text):
    """成对 ASCII 双引号 → 中文引号（中文段落禁用 U+0022）。"""
    out, open_q = [], True
    for ch in text:
        if ch == '"':
            out.append('\u201c' if open_q else '\u201d')
            open_q = not open_q
        else:
            out.append(ch)
    return ''.join(out)

def add_bold_runs(p, text, size=None):
    """支持 **加粗** 行内标记；反引号代码标记去除；ASCII 双引号转中文引号。"""
    text = cn_quotes(text.replace('`', ''))
    parts = text.split('**')
    for i, part in enumerate(parts):
        if not part:
            continue
        r = p.add_run(part)
        r.bold = (i % 2 == 1)
        if size:
            r.font.size = Pt(size)
    return p

def body(text):
    p = para()
    p.paragraph_format.first_line_indent = Pt(24)
    add_bold_runs(p, text)

def bullet(text):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.first_line_indent = Pt(0)
    add_bold_runs(p, text)
    return p

def quote(text):
    p = para()
    p.paragraph_format.first_line_indent = Pt(0)
    r = p.add_run(cn_quotes(text))
    r.font.size = Pt(11)
    r.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
    return p

def ascii_block(lines):
    for line in lines:
        p = para()
        r = p.add_run(line)
        r.font.name = 'Consolas'
        r.font.size = Pt(9)
        r._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

def _zero_first_line_indent(p):
    pPr = p._p.get_or_add_pPr()
    ind = pPr.find(qn('w:ind'))
    if ind is None:
        ind = OxmlElement('w:ind'); pPr.append(ind)
    ind.set(qn('w:firstLineChars'), '0')
    ind.set(qn('w:firstLine'), '0')

def _shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto'); shd.set(qn('w:fill'), color)
    tcPr.append(shd)

def _cantSplit(cell):
    cell._tc.get_or_add_tcPr()
    trPr = cell._tc.getparent().get_or_add_trPr()
    trPr.append(OxmlElement('w:cantSplit'))

def _tblHeader(t):
    trPr = t.rows[0]._tr.get_or_add_trPr()
    th = OxmlElement('w:tblHeader'); th.set(qn('w:val'), 'true')
    trPr.append(th)

def table(headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Table Grid'
    t.autofit = False
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ''
        hp = hdr[i].paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        hr = hp.add_run(h); hr.bold = True
        hr.font.size = Pt(10.5); hr.font.name = 'Times New Roman'
        hr._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        _shade(hdr[i], 'D9D9D9')
        hdr[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _zero_first_line_indent(hp)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ''
            cp = cells[i].paragraphs[0]
            if len(v) <= 12:
                cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                cp.alignment = WD_ALIGN_PARAGRAPH.LEFT
            add_bold_runs(cp, v, size=10.5)
            cells[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _zero_first_line_indent(cp)
            _cantSplit(cells[i])
    _tblHeader(t)
    return t

def add_field(p, instr):
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), instr)
    p._p.append(fld)

# ============ 封面（Section 1，无页码） ============
p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(160)
tp = doc.add_paragraph(style='Title'); tp.add_run('微言 · AI 银行智能体')
sp = doc.add_paragraph(style='Subtitle')
if 'Subtitle' in [s.name for s in doc.styles]:
    sp.text = ''
    r = sp.add_run('技 术 文 档'); r.font.size = Pt(14); r.font.name = 'Times New Roman'
    r._element.rPr.rFonts.set(qn('w:eastAsia'), '楷体')
sub = doc.add_paragraph()
r = sub.add_run('2026 深圳国际金融科技大赛（微众银行）· AI 银行智能体赛道\n作品资料 2/5 · 系统架构 / 核心算法 / 安全设计\n版本 v2.1 · 2026 年 10 月 4 日')
r.font.size = Pt(12)
sub.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
sub.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE

# ============ 目录页（Section 2，无页码） ============
doc.add_section()
toc_p = doc.add_paragraph()
r = toc_p.add_run('目  录'); r.bold = True; r.font.size = Pt(16); r.font.name = 'Times New Roman'
r._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
toc_p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
toc_p.paragraph_format.first_line_indent = Pt(0)
tp2 = doc.add_paragraph()
add_field(tp2, r'TOC \o "1-2" \h \z \u')

# ============ 正文（Section 3，页码从 1） ============
doc.add_section()
sec3 = doc.sections[-1]
fp = sec3.footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
add_field(fp, r'PAGE')
sectPr = sec3._sectPr
pgNumType = OxmlElement('w:pgNumType'); pgNumType.set(qn('w:start'), '1')
sectPr.append(pgNumType)

# ============ md 解析 → 正文 ============
with io.open(MD, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()

def split_row(line):
    s = line.strip()
    if not (s.startswith('|') and s.endswith('|')):
        return None
    cells = [c.strip() for c in s[1:-1].split('|')]
    if all(set(c) <= set('-:') for c in cells):
        return None  # 分隔行
    return cells

i = 0
in_code = False
code_buf = []
table_buf = []
n = len(lines)
while i < n:
    line = lines[i]
    if in_code:
        if line.strip().startswith('```'):
            ascii_block(code_buf)
            code_buf = []
            in_code = False
        else:
            code_buf.append(line)
        i += 1
        continue
    if line.strip().startswith('```'):
        in_code = True
        i += 1
        continue
    if line.strip().startswith('|'):
        rows = []
        hdr = None
        while i < n and lines[i].strip().startswith('|'):
            cells = split_row(lines[i])
            if cells is not None:
                if hdr is None:
                    hdr = cells
                else:
                    rows.append(cells)
            i += 1
        if hdr:
            table(hdr, rows)
        continue
    s = line.strip()
    if not s or s == '---':
        i += 1
        continue
    if s.startswith('# '):
        i += 1  # 文档主标题由封面承担
        continue
    if s.startswith('## '):
        doc.add_heading(cn_quotes(s[3:].strip()), level=1)
        i += 1
        continue
    if s.startswith('### '):
        doc.add_heading(cn_quotes(s[4:].strip()), level=2)
        i += 1
        continue
    if s.startswith('> '):
        quote(s[2:].strip())
        i += 1
        continue
    if s.startswith('- '):
        bullet(s[2:].strip())
        i += 1
        continue
    body(s)
    i += 1

doc.save(OUT)
print("docx generated:", OUT)
