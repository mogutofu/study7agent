# -*- coding: utf-8 -*-
"""OOXML 文档能力：docx / xlsx / pptx。

面向学校教学一体机场景：
  - 课件（.pptx）：从大纲文本生成标准 OOXML 演示文稿，PowerPoint / WPS 演示可直接打开编辑
  - 教案、通知（.docx）：支持标题层级、表格、列表、行内加粗与等宽
  - 成绩表（.xlsx）：表头加粗、列宽自适应、首行冻结

只使用 Python 3.8 标准库（zipfile + xml）。生成的包都可以通过 check_package() 做结构自检：
所有关系目标存在、内容类型覆盖完整、每个部件都是合法 XML。
"""

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

# --------------------------------------------------------------------------
# 命名空间与通用工具
# --------------------------------------------------------------------------

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_PR = "http://schemas.openxmlformats.org/package/2006/relationships"

XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

CT_PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
CT_SLIDE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
CT_SLIDE_MASTER = "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"
CT_SLIDE_LAYOUT = "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"
CT_PRES_PROPS = "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"
CT_VIEW_PROPS = "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"
CT_TABLE_STYLES = "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"
CT_THEME = "application/vnd.openxmlformats-officedocument.theme+xml"
CT_DOCUMENT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CT_DOC_STYLES = "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
CT_SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
CT_WORKBOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"

REL_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
REL_SLIDE_MASTER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster"
REL_SLIDE_LAYOUT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
REL_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
REL_PRES_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps"
REL_VIEW_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps"
REL_TABLE_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_WORKSHEET = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"

EMU_PER_INCH = 914400
SLIDE_W = 12192000          # 16:9，教学一体机与投影仪通用
SLIDE_H = 6858000
FONT_CN = "微软雅黑"
FONT_MONO = "Consolas"

SLIDE_BG = "FFFFFF"
TITLE_COLOR = "1F3864"
TABLE_HEAD_BG = "4472C4"
TABLE_LINE = "BFBFBF"


def xml_escape(text):
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _zip_bytes(parts):
    """parts: [(name, str|bytes)] -> zip 字节流。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts:
            data = body.encode("utf-8") if isinstance(body, str) else body
            archive.writestr(name, data)
    return buffer.getvalue()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------
# 包结构自检（生成结果的正确性保障，测试与排障都用它）
# --------------------------------------------------------------------------

def check_package(data):
    """校验 OOXML 包结构，返回问题列表（空列表 = 通过）。"""
    problems = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return ["不是合法的 zip 包：%s" % exc]

    names = set(archive.namelist())
    parts = {}
    for name in names:
        if name.endswith("/"):
            continue
        if name.endswith(".xml") or name.endswith(".rels"):
            try:
                parts[name] = ET.fromstring(archive.read(name))
            except ET.ParseError as exc:
                problems.append("%s 不是合法 XML：%s" % (name, exc))

    if "[Content_Types].xml" not in names:
        problems.append("缺少 [Content_Types].xml")
        return problems

    # 内容类型覆盖检查
    content_types = parts.get("[Content_Types].xml")
    defaults = set()
    overrides = set()
    if content_types is not None:
        for node in content_types:
            if _local(node.tag) == "Default":
                defaults.add("." + (node.get("Extension") or "").lower())
            elif _local(node.tag) == "Override":
                overrides.add((node.get("PartName") or "").lstrip("/"))
    known_ct = {"[Content_Types].xml"}
    for name in names:
        if name in known_ct or name.endswith("/"):
            continue
        base = os.path.basename(name)
        extension = ("." + base.rsplit(".", 1)[1].lower()) if "." in base else ""
        if name in overrides or extension in defaults:
            continue
        problems.append("%s 没有被内容类型覆盖" % name)

    # 关系目标检查
    for name in sorted(names):
        if not name.endswith(".rels"):
            continue
        root = parts.get(name)
        if root is None:
            continue
        base = os.path.dirname(os.path.dirname(name))
        for node in root:
            if _local(node.tag) != "Relationship":
                continue
            if (node.get("TargetMode") or "") == "External":
                continue
            target = (node.get("Target") or "").lstrip("/")
            resolved = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
            if resolved not in names:
                problems.append("%s 的关系目标不存在：%s" % (name, resolved))

    # 演示文稿的幻灯片清单检查
    presentation = parts.get("ppt/presentation.xml")
    rels = parts.get("ppt/_rels/presentation.xml.rels")
    if presentation is not None and rels is not None:
        targets = {}
        for node in rels:
            if _local(node.tag) == "Relationship":
                targets[node.get("Id")] = (node.get("Target") or "").lstrip("/")

        def referenced(tag_name):
            found = []
            for node in presentation.iter():
                if _local(node.tag) != tag_name:
                    continue
                found.append(node.get("{%s}id" % NS_R) or node.get("id"))
            return found

        slide_refs = referenced("sldId")
        master_refs = referenced("sldMasterId")
        for rid in slide_refs:
            if rid not in targets:
                problems.append("presentation.xml 引用了不存在的关系 %s" % rid)
            elif not re.match(r"^ppt/slides/slide\d+\.xml$", "ppt/" + targets[rid]):
                problems.append("sldId %s 指向的不是幻灯片：%s" % (rid, targets[rid]))
            elif ("ppt/" + targets[rid]) not in names:
                problems.append("幻灯片部件缺失：%s" % targets[rid])
        for rid in master_refs:
            if rid not in targets:
                problems.append("presentation.xml 引用了不存在的母版关系 %s" % rid)
            elif not targets[rid].startswith("slideMasters/"):
                problems.append("sldMasterId %s 指向的不是母版：%s" % (rid, targets[rid]))
        overlap = set(slide_refs) & set(master_refs)
        if overlap:
            problems.append("同一个关系被母版和幻灯片同时引用：%s" % sorted(overlap))
        if not master_refs:
            problems.append("presentation.xml 没有引用任何幻灯片母版")
    return problems


# --------------------------------------------------------------------------
# Markdown 行内解析（docx 与 pptx 共用）
# --------------------------------------------------------------------------

def parse_inline(text):
    """把 **粗体**、*斜体*、`等宽` 拆成 [(text, style)]，style ∈ {'', 'b', 'i', 'c'}。"""
    runs = []
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*\n]+\*)")
    position = 0
    for match in pattern.finditer(text or ""):
        if match.start() > position:
            runs.append((text[position:match.start()], ""))
        token = match.group(0)
        if token.startswith("**"):
            runs.append((token[2:-2], "b"))
        elif token.startswith("`"):
            runs.append((token[1:-1], "c"))
        else:
            runs.append((token[1:-1], "i"))
        position = match.end()
    if position < len(text or ""):
        runs.append((text[position:], ""))
    return [(value, style) for value, style in runs if value != ""]


def parse_bullet(line):
    """识别列表行，返回 (级别, 类型, 文本)；不是列表返回 None。类型：bullet/number。"""
    match = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
    if not match:
        return None
    indent = len(match.group(1).replace("\t", "  "))
    kind = "number" if match.group(2)[0].isdigit() else "bullet"
    return (min(4, indent // 2), kind, match.group(3).strip())


def parse_markdown_table(content):
    """提取第一个 Markdown 表格，返回行列表。"""
    rows = []
    for line in (content or "").split("\n"):
        text = line.strip()
        if not text.startswith("|") or not text.endswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if all(not cell or not cell.strip("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def parse_rows(content):
    """把内容解析成二维表：优先 Markdown 表格，其次 CSV，最后按行拆分。"""
    import csv
    rows = parse_markdown_table(content)
    if rows:
        return rows
    try:
        parsed = [row for row in csv.reader(io.StringIO(content or ""))]
        if parsed:
            return parsed
    except csv.Error:
        pass
    fallback = [[line] for line in (content or "").split("\n") if line.strip()]
    return fallback or [[""]]


# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------

DOCX_STYLES = XML_HEAD + (
    '<w:styles xmlns:w="%s">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="%s"/>'
    '<w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:before="0" w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    "</w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:before="240" w:after="240"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="44"/><w:color w:val="%s"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="240" w:after="120"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="%s"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="200" w:after="100"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="%s"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:pPr><w:outlineLvl w:val="2"/><w:spacing w:before="160" w:after="80"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="420"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="420"/></w:pPr><w:rPr><w:i/><w:color w:val="595959"/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="Code"><w:name w:val="Code"/>'
    '<w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s"/><w:sz w:val="20"/></w:rPr></w:style>'
    "</w:styles>"
) % (NS_W, FONT_CN, TITLE_COLOR, TITLE_COLOR, "2E5496", FONT_MONO, FONT_MONO)

DOCX_CONTENT_TYPES = XML_HEAD + (
    '<Types xmlns="%s">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="%s"/>'
    '<Override PartName="/word/styles.xml" ContentType="%s"/>'
    "</Types>"
) % (NS_CT, CT_DOCUMENT, CT_DOC_STYLES)

DOCX_RELS = XML_HEAD + (
    '<Relationships xmlns="%s">'
    '<Relationship Id="rId1" Type="%s" Target="word/document.xml"/>'
    "</Relationships>"
) % (NS_PR, REL_OFFICE_DOCUMENT)

DOCX_DOCUMENT_RELS = XML_HEAD + (
    '<Relationships xmlns="%s">'
    '<Relationship Id="rId1" Type="%s" Target="styles.xml"/>'
    "</Relationships>"
) % (NS_PR, REL_STYLES)


def _docx_run(text, style):
    properties = ""
    if style == "b":
        properties = "<w:rPr><w:b/></w:rPr>"
    elif style == "i":
        properties = "<w:rPr><w:i/></w:rPr>"
    elif style == "c":
        properties = '<w:rPr><w:rStyle w:val="Code"/></w:rPr>'
    return "<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (properties, xml_escape(text))


def _docx_paragraph(text, style=None, indent=0, prefix=""):
    properties = []
    if style:
        properties.append('<w:pStyle w:val="%s"/>' % style)
    if indent:
        properties.append('<w:ind w:left="%d"/>' % (420 + indent * 360))
    ppr = "<w:pPr>%s</w:pPr>" % "".join(properties) if properties else ""
    runs = "".join(_docx_run(value, kind) for value, kind in parse_inline(text))
    if prefix:
        runs = _docx_run(prefix, "") + runs
    return "<w:p>%s%s</w:p>" % (ppr, runs or "<w:r><w:t/></w:r>")


def _docx_table(rows):
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    width = int(9360 / max(1, columns))
    borders = "".join(
        '<w:%s w:val="single" w:sz="6" w:space="0" w:color="%s"/>' % (edge, TABLE_LINE)
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    xml = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
           "<w:tblBorders>%s</w:tblBorders>"
           '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1"'
           ' w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>%s</w:tblGrid>'
           % (borders, "".join('<w:gridCol w:w="%d"/>' % width for _ in range(columns)))]
    for row_index, row in enumerate(rows):
        cells = []
        for column in range(columns):
            value = row[column] if column < len(row) else ""
            bold = row_index == 0
            shading = ('<w:shd w:val="clear" w:color="auto" w:fill="DEEAF6"/>' if bold else "")
            runs = "".join(
                _docx_run(text, "b" if bold else kind) for text, kind in (parse_inline(value) or [("", "")])
            ) or "<w:r><w:t/></w:r>"
            cells.append(
                '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>'
                % (width, shading, "<w:p>%s</w:p>" % runs))
        xml.append("<w:tr>%s</w:tr>" % "".join(cells))
    xml.append("</w:tbl>")
    xml.append(_docx_paragraph(""))
    return "".join(xml)


def build_docx(content):
    """按 Markdown 结构生成 docx：标题层级、列表、表格、行内样式。"""
    body = []
    lines = (content or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\s*```", line):
            index += 1
            code = []
            while index < len(lines) and not re.match(r"^\s*```\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            index += 1
            for code_line in code:
                body.append(_docx_paragraph(code_line, indent=1, prefix=""))
                body[-1] = body[-1].replace("<w:r>", '<w:r><w:rPr><w:rStyle w:val="Code"/></w:rPr>', 1)
            continue
        if re.match(r"^\s*\|.*\|\s*$", line):
            table_lines = []
            while index < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[index]):
                table_lines.append(lines[index])
                index += 1
            body.append(_docx_table(parse_markdown_table("\n".join(table_lines))))
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            style = {1: "Title", 2: "Heading1", 3: "Heading2"}.get(level, "Heading3")
            body.append(_docx_paragraph(heading.group(2).strip(), style=style))
            index += 1
            continue
        bullet = parse_bullet(line)
        if bullet:
            level, kind, text = bullet
            prefix = "• " if kind == "bullet" else ""
            body.append(_docx_paragraph(text, style="ListParagraph", indent=level, prefix=prefix))
            index += 1
            continue
        if line.strip().startswith(">"):
            body.append(_docx_paragraph(line.strip()[1:].strip(), style="Quote"))
            index += 1
            continue
        if re.match(r"^\s*([-*_])\s*\1\s*\1", line):
            body.append(_docx_paragraph("─" * 30))
            index += 1
            continue
        if not line.strip():
            index += 1
            continue
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(
                r"^\s*(#{1,4}\s|```|\||>|([-*+]|\d+[.)])\s)", lines[index]):
            paragraph.append(lines[index])
            index += 1
        body.append(_docx_paragraph(" ".join(part.strip() for part in paragraph)))

    document = XML_HEAD + (
        '<w:document xmlns:w="%s"><w:body>%s'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
        'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr></w:body></w:document>'
    ) % (NS_W, "".join(body))
    return _zip_bytes([
        ("[Content_Types].xml", DOCX_CONTENT_TYPES),
        ("_rels/.rels", DOCX_RELS),
        ("word/document.xml", document),
        ("word/styles.xml", DOCX_STYLES),
        ("word/_rels/document.xml.rels", DOCX_DOCUMENT_RELS),
    ])


def read_docx(path):
    """提取 docx 文本：段落换行、表格单元格用制表符分隔。"""
    out = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist()
                 if name.lower() == "word/document.xml"
                 or name.lower().startswith("word/header")
                 or name.lower().startswith("word/footer")]
        for name in names:
            pieces = []
            with archive.open(name) as handle:
                for event, element in ET.iterparse(handle, events=("end",)):
                    tag = _local(element.tag)
                    if tag == "t" and element.text:
                        pieces.append(element.text)
                    elif tag in ("p", "tr"):
                        pieces.append("\n")
                    elif tag == "tc":
                        pieces.append("\t")
                    element.clear()
            out.append("".join(pieces).strip())
    return "\n".join(part for part in out if part).strip()


# --------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------

XLSX_CONTENT_TYPES = XML_HEAD + (
    '<Types xmlns="%s">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="%s"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="%s"/>'
    "</Types>"
) % (NS_CT, CT_WORKBOOK, CT_SHEET)

XLSX_RELS = XML_HEAD + (
    '<Relationships xmlns="%s">'
    '<Relationship Id="rId1" Type="%s" Target="xl/workbook.xml"/>'
    "</Relationships>"
) % (NS_PR, REL_OFFICE_DOCUMENT)

XLSX_WORKBOOK_RELS = XML_HEAD + (
    '<Relationships xmlns="%s">'
    '<Relationship Id="rId1" Type="%s" Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
) % (NS_PR, REL_WORKSHEET)


def column_name(number):
    name = ""
    while number > 0:
        number -= 1
        name = chr(ord("A") + number % 26) + name
        number //= 26
    return name


def _display_width(text):
    width = 0
    for char in str(text or ""):
        width += 2 if ord(char) > 0x2E80 else 1
    return width


def build_xlsx(rows, sheet="Sheet1", header=True):
    """生成 xlsx：首行加粗并冻结、列宽按内容自适应。"""
    sheet = sheet or "Sheet1"
    rows = [list(row) for row in (rows or [[""]])]
    columns = max(len(row) for row in rows) if rows else 1

    widths = []
    for column in range(columns):
        longest = 6
        for row in rows:
            if column < len(row):
                longest = max(longest, min(48, _display_width(row[column]) + 2))
        widths.append(longest)

    body = []
    for row_index, row in enumerate(rows):
        cells = []
        for column in range(columns):
            value = row[column] if column < len(row) else ""
            style = ' s="1"' if (header and row_index == 0) else ""
            cells.append('<c r="%s%d"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                         % (column_name(column + 1), row_index + 1, style, xml_escape(value)))
        body.append('<row r="%d">%s</row>' % (row_index + 1, "".join(cells)))

    column_xml = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>'
                         % (index + 1, index + 1, widths[index]) for index in range(columns))
    freeze = ('<sheetViews><sheetView workbookViewId="0">'
              '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
              "</sheetView></sheetViews>") if (header and len(rows) > 1) else ""
    worksheet = XML_HEAD + (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "%s<cols>%s</cols><sheetData>%s</sheetData></worksheet>"
    ) % (freeze, column_xml, "".join(body))

    styles_xml = XML_HEAD + (
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="%s"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="%s"/></font>'
        "</fonts>"
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF4472C4"/><bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        "</cellXfs>"
        "</styleSheet>"
    ) % (FONT_CN, FONT_CN)

    workbook = XML_HEAD + (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="%s"><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ) % (NS_R, xml_escape(sheet))

    return _zip_bytes([
        ("[Content_Types].xml", XLSX_CONTENT_TYPES),
        ("_rels/.rels", XLSX_RELS),
        ("xl/_rels/workbook.xml.rels", XLSX_WORKBOOK_RELS),
        ("xl/workbook.xml", workbook),
        ("xl/styles.xml", styles_xml),
        ("xl/worksheets/sheet1.xml", worksheet),
    ])


def read_xlsx(path):
    """提取 xlsx 内容：每个工作表前加一行 '## 工作表名'，单元格用制表符分隔。"""
    shared = []
    sheets = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            if name.lower() == "xl/sharedstrings.xml":
                with archive.open(name) as handle:
                    text = handle.read().decode("utf-8", "replace")
                shared = [re.sub(r"<[^>]+>", "", item) for item in re.findall(r"<si>(.*?)</si>", text, re.S)]
        for name in sorted(names):
            lower = name.lower()
            if not (lower.startswith("xl/worksheets/") and lower.endswith(".xml")):
                continue
            with archive.open(name) as handle:
                root = ET.parse(handle).getroot()
            rows = []
            for row_element in root.iter():
                if _local(row_element.tag) != "row":
                    continue
                row = []
                for cell in row_element:
                    if _local(cell.tag) != "c":
                        continue
                    reference = ""
                    cell_type = ""
                    for key, value in cell.attrib.items():
                        if _local(key) == "r":
                            reference = value
                        elif _local(key) == "t":
                            cell_type = value
                    pieces = [node.text for node in cell.iter()
                              if _local(node.tag) in ("v", "t") and node.text]
                    raw = "".join(pieces)
                    if cell_type == "s":
                        try:
                            position = int(raw.strip())
                            raw = shared[position] if 0 <= position < len(shared) else raw
                        except ValueError:
                            pass
                    column = 0
                    for char in reference:
                        if char.isalpha():
                            column = column * 26 + (ord(char.upper()) - ord("A") + 1)
                        else:
                            break
                    while column > 0 and len(row) < column - 1:
                        row.append("")
                    row.append(raw)
                if row:
                    rows.append(row)
            sheets.append("## %s\n%s" % (os.path.basename(name),
                                        "\n".join("\t".join(cells) for cells in rows)))
    return "\n".join(sheets).strip()


# --------------------------------------------------------------------------
# PPTX：大纲解析
# --------------------------------------------------------------------------

def parse_slides(content):
    """把大纲文本解析成幻灯片结构。

    约定（与 read_pptx 的输出一致，便于"读旧稿 → 改 → 写新稿"）：
        # 标题            新的一张幻灯片
        ## 小标题         幻灯片内的小标题
        - 要点 / 1. 步骤   项目符号 / 编号（缩进 2 空格降一级）
        | a | b |         幻灯片上的表格
        ``` 围栏 ```      等宽文本块
        > 讲稿            演讲者备注（单独生成讲稿文件）
        ---               强制分页
    第一个 `#` 之前的文字会被忽略，因此可以在大纲前面写一句说明。
    """
    text = (content or "").replace("\r\n", "\n")
    lines = text.split("\n")
    if not re.search(r"^\s*#\s+\S", text, re.M) and not re.search(r"^\s*-{3,}\s*$", text, re.M):
        # 没有使用任何分页标记：整段作为一张幻灯片，首行当标题
        blocks = []
        heading = ""
        for line in lines:
            if not line.strip():
                continue
            bullet = parse_bullet(line)
            if bullet and heading:
                level, kind, value = bullet
                if blocks and blocks[-1]["type"] == "bullets":
                    blocks[-1]["items"].append((level, kind, value))
                else:
                    blocks.append({"type": "bullets", "items": [(level, kind, value)]})
            elif not heading:
                heading = line.strip()
            else:
                blocks.append({"type": "paragraph", "text": line.strip()})
        return [{"title": heading, "blocks": blocks, "notes": []}]

    slides = []
    current = {"title": "", "blocks": [], "notes": []}
    started = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\s*```", line):
            index += 1
            code = []
            while index < len(lines) and not re.match(r"^\s*```\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            index += 1
            current["blocks"].append({"type": "code", "text": "\n".join(code)})
            started = True
            continue
        if re.match(r"^\s*\|.*\|\s*$", line):
            table_lines = []
            while index < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[index]):
                table_lines.append(lines[index])
                index += 1
            rows = parse_markdown_table("\n".join(table_lines))
            if rows:
                current["blocks"].append({"type": "table", "rows": rows})
                started = True
            continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            if started or current["title"] or current["blocks"] or current["notes"]:
                slides.append(current)
            current = {"title": "", "blocks": [], "notes": []}
            started = True
            index += 1
            continue
        title = re.match(r"^#\s+(.*)$", line)
        if title:
            if started or current["title"] or current["blocks"] or current["notes"]:
                slides.append(current)
            current = {"title": title.group(1).strip(), "blocks": [], "notes": []}
            started = True
            index += 1
            continue
        sub = re.match(r"^#{2,6}\s+(.*)$", line)
        if sub:
            current["blocks"].append({"type": "heading", "text": sub.group(1).strip()})
            started = True
            index += 1
            continue
        if line.strip().startswith(">"):
            current["notes"].append(line.strip()[1:].strip())
            started = True
            index += 1
            continue
        bullet = parse_bullet(line)
        if bullet:
            level, kind, value = bullet
            blocks = current["blocks"]
            if blocks and blocks[-1]["type"] == "bullets":
                blocks[-1]["items"].append((level, kind, value))
            else:
                blocks.append({"type": "bullets", "items": [(level, kind, value)]})
            started = True
            index += 1
            continue
        if not line.strip():
            index += 1
            continue
        if not started:
            # 第一个 # 之前的说明文字直接忽略
            index += 1
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(
                r"^\s*(#{1,6}\s|```|\||>|([-*+]|\d+[.)])\s|-{3,}\s*$)", lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        current["blocks"].append({"type": "paragraph", "text": " ".join(paragraph)})

    if started and (current["title"] or current["blocks"] or current["notes"]):
        slides.append(current)
    return [slide for slide in slides if slide["title"] or slide["blocks"] or slide["notes"]]


# --------------------------------------------------------------------------
# PPTX：生成
# --------------------------------------------------------------------------

def _text_body(paragraphs, body_props=None, body_inner=""):
    return "<p:txBody><a:bodyPr%s>%s</a:bodyPr><a:lstStyle/>%s</p:txBody>" % (
        body_props or "", body_inner, "".join(paragraphs))


def _run_xml(text, size, bold=False, monospace=False, color=None, italic=False):
    properties = ['lang="zh-CN"', 'sz="%d"' % int(size * 100)]
    if bold:
        properties.append('b="1"')
    if italic:
        properties.append('i="1"')
    properties.append('dirty="0"')
    inner = ""
    if color:
        inner += '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % color
    typeface = FONT_MONO if monospace else FONT_CN
    inner += '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/>' % (
        typeface, typeface, typeface)
    return "<a:r><a:rPr %s>%s</a:rPr><a:t>%s</a:t></a:r>" % (
        " ".join(properties), inner, xml_escape(text))


def _paragraph_runs(text, size, bold=False, monospace=False, color=None):
    runs = parse_inline(text)
    if not runs:
        runs = [("", "")]
    return "".join(
        _run_xml(value, size, bold=bold or kind == "b", monospace=monospace or kind == "c",
                 color=color, italic=(kind == "i"))
        for value, kind in runs)


def _shape_xml(shape_id, name, x, y, cx, cy, paragraphs, body_props=None, body_inner=""):
    return (
        "<p:sp><p:nvSpPr>"
        '<p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
        "</p:nvSpPr>"
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        "%s</p:sp>"
    ) % (shape_id, xml_escape(name), x, y, cx, cy, _text_body(paragraphs, body_props, body_inner))


def _bullets_paragraphs(items, base_size=28):
    """教学一体机场景：正文默认 28pt，逐级递减，保证后排也能看清。"""
    paragraphs = []
    for level, kind, text in items:
        size = max(18, base_size - level * 4)
        bullet = ('<a:buChar char="•"/>' if kind == "bullet"
                  else '<a:buAutoNum type="arabicPeriod"/>')
        paragraphs.append(
            '<a:p><a:pPr marL="%d" indent="-342900" lvl="%d">%s</a:pPr>%s</a:p>'
            % (342900 * (level + 1), level, bullet, _paragraph_runs(text, size)))
    return paragraphs


def _plain_paragraph(text, size, bold=False, monospace=False, color=None):
    """无项目符号的段落：显式 buNone，避免继承母版的自动项目符号。"""
    return ('<a:p><a:pPr algn="l"><a:buNone/></a:pPr>%s</a:p>'
            % _paragraph_runs(text, size, bold=bold, monospace=monospace, color=color))


def _table_frame_xml(shape_id, name, x, y, cx, cy, rows):
    columns = max(len(row) for row in rows)
    rows = [list(row) + [""] * (columns - len(row)) for row in rows]
    grid_width = int(cx / max(1, columns))
    row_height = 370000
    grid = "".join('<a:gridCol w="%d"/>' % grid_width for _ in range(columns))
    trs = []
    for row_index, row in enumerate(rows):
        cells = []
        for value in row:
            header = row_index == 0
            # tcPr 里线条必须在填充之前（OOXML 元素顺序）
            line = ('<a:lnB w="9525" cap="flat" cmpd="sng" algn="ctr">'
                    '<a:solidFill><a:srgbClr val="%s"/></a:solidFill><a:prstDash val="solid"/></a:lnB>'
                    % TABLE_LINE)
            fill = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % TABLE_HEAD_BG) if header else ""
            paragraph = '<a:p><a:pPr algn="l"/>%s</a:p>' % _paragraph_runs(
                value, 14, bold=header, color="FFFFFF" if header else None)
            cells.append(
                '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>%s</a:txBody>'
                '<a:tcPr marL="91440" marR="91440" marT="45720" marB="45720">%s%s</a:tcPr></a:tc>'
                % (paragraph, line, fill))
        trs.append('<a:tr h="%d">%s</a:tr>' % (row_height, "".join(cells)))
    return (
        '<p:graphicFrame><p:nvGraphicFramePr>'
        '<p:cNvPr id="%d" name="%s"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/>'
        "</p:nvGraphicFramePr>"
        '<p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        '<a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>%s</a:tblGrid>%s</a:tbl>'
        "</a:graphicData></a:graphic></p:graphicFrame>"
    ) % (shape_id, xml_escape(name), x, y, cx, cy, grid, "".join(trs))


def _slide_xml(slide, index):
    shapes = []
    shape_id = 2
    title = slide.get("title") or ""
    blocks = list(slide.get("blocks") or [])
    if not title and blocks:
        first = blocks[0]
        if first["type"] in ("paragraph", "heading"):
            title = first["text"]
            blocks = blocks[1:]
    title_paragraphs = [
        _plain_paragraph(title or " ", 36, bold=True, color=TITLE_COLOR)
    ]
    shapes.append(_shape_xml(shape_id, "Title %d" % index,
                             838200, 457200, SLIDE_W - 1676400, 1104900,
                             title_paragraphs,
                             ' wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720"'))
    shape_id += 1
    body_top = 1752600
    body_height = SLIDE_H - body_top - 685800
    flow = []
    for block in blocks:
        if block["type"] == "bullets":
            flow.append(("bullets", _bullets_paragraphs(block["items"])))
        elif block["type"] == "heading":
            flow.append(("heading", [_plain_paragraph(block["text"], 28, bold=True,
                                                      color="2E5496")]))
        elif block["type"] == "code":
            flow.append(("code", [_plain_paragraph(line or " ", 18, monospace=True)
                                  for line in block["text"].split("\n")]))
        elif block["type"] == "table":
            flow.append(("table", block["rows"]))
        else:
            flow.append(("paragraph", [_plain_paragraph(block["text"], 24)]))

    tables = [item for item in flow if item[0] == "table"]
    texts = [item for item in flow if item[0] != "table"]
    line_index = 0
    if texts:
        paragraphs = []
        for _kind, group in texts:
            paragraphs.extend(group)
            line_index += len(group)
        shapes.append(_shape_xml(shape_id, "Body %d" % index,
                                 838200, body_top, SLIDE_W - 1676400,
                                 body_height if not tables else int(body_height * 0.62),
                                 paragraphs,
                                 ' wrap="square" lIns="91440" tIns="45720" rIns="91440"'
                                 ' bIns="45720"',
                                 "<a:normAutofit/>"))
        shape_id += 1
    # 表格紧跟在文字下方，而不是固定贴在页面底部，避免中间留下大片空白
    table_top = body_top + min(int(body_height * 0.5), line_index * 440000 + 180000)
    for _kind, rows in tables:
        table_height = min(body_height, 420000 * len(rows) + 120000)
        top = min(table_top, SLIDE_H - 685800 - table_height)
        top = max(top, body_top)
        shapes.append(_table_frame_xml(shape_id, "Table %d" % index,
                                       838200, top, SLIDE_W - 1676400, table_height, rows))
        shape_id += 1
        table_top = top + table_height + 120000

    tree = (
        "<p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/>"
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "%s</p:spTree>" % "".join(shapes)
    )
    return XML_HEAD + (
        '<p:sld xmlns:a="%s" xmlns:r="%s" xmlns:p="%s"><p:cSld>%s</p:cSld>'
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    ) % (NS_A, NS_R, NS_P, tree)


def _slide_rels():
    return XML_HEAD + (
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="../slideLayouts/slideLayout1.xml"/>'
        "</Relationships>"
    ) % (NS_PR, REL_SLIDE_LAYOUT)


def _theme_xml():
    accents = ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"]
    color_scheme = (
        '<a:clrScheme name="Office">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
        + "".join('<a:accent%d><a:srgbClr val="%s"/></a:accent%d>' % (i + 1, value, i + 1)
                  for i, value in enumerate(accents))
        + '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
    )
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = ('<a:ln w="6350" cap="flat" cmpd="sng" algn="ctr">%s'
            '<a:prstDash val="solid"/></a:ln>' % fill)
    return XML_HEAD + (
        '<a:theme xmlns:a="%s" name="Study7Agent 教学主题"><a:themeElements>'
        "%s"
        '<a:fontScheme name="Office">'
        '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface="%s"/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="%s"/><a:cs typeface=""/></a:minorFont>'
        "</a:fontScheme>"
        '<a:fmtScheme name="Office">'
        "<a:fillStyleLst>%s%s%s</a:fillStyleLst>"
        "<a:lnStyleLst>%s%s%s</a:lnStyleLst>"
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        "<a:bgFillStyleLst>%s%s%s</a:bgFillStyleLst>"
        "</a:fmtScheme></a:themeElements>"
        "<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>"
    ) % (NS_A, color_scheme, FONT_CN, FONT_CN, fill, fill, fill, line, line, line, fill, fill, fill)


def _level_properties(count=9):
    return "".join(
        '<a:lvl%dpPr marL="%d" indent="-342900" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1"'
        ' latinLnBrk="0" hangingPunct="1"><a:defRPr sz="%d" kern="1200">%s</a:defRPr></a:lvl%dpPr>'
        % (level, 342900 * level, max(1600, 2800 - (level - 1) * 200),
           '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
           '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/>', level)
        for level in range(1, count + 1))


def _slide_master_xml():
    tree = (
        "<p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "</p:spTree>"
    )
    return XML_HEAD + (
        '<p:sldMaster xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
        '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
        '<a:effectLst/></p:bgPr></p:bg>%s</p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2"'
        ' accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6"'
        ' hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        "<p:txStyles>"
        '<p:titleStyle><a:lvl1pPr algn="l"><a:defRPr sz="3200" b="1">'
        '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
        '<a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/><a:cs typeface="+mj-cs"/>'
        "</a:defRPr></a:lvl1pPr></p:titleStyle>"
        "<p:bodyStyle>%s</p:bodyStyle>"
        '<p:otherStyle><a:defPPr><a:defRPr lang="zh-CN"/></a:defPPr></p:otherStyle>'
        "</p:txStyles></p:sldMaster>"
    ) % (NS_A, NS_R, NS_P, SLIDE_BG, tree, _level_properties())


def _slide_master_rels():
    return XML_HEAD + (
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="../slideLayouts/slideLayout1.xml"/>'
        '<Relationship Id="rId2" Type="%s" Target="../theme/theme1.xml"/>'
        "</Relationships>"
    ) % (NS_PR, REL_SLIDE_LAYOUT, REL_THEME)


def _slide_layout_xml():
    tree = (
        "<p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "</p:spTree>"
    )
    return XML_HEAD + (
        '<p:sldLayout xmlns:a="%s" xmlns:r="%s" xmlns:p="%s" type="blank" preserve="1">'
        '<p:cSld name="标题和内容">%s</p:cSld>'
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    ) % (NS_A, NS_R, NS_P, tree)


def _slide_layout_rels():
    return XML_HEAD + (
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="../slideMasters/slideMaster1.xml"/>'
        "</Relationships>"
    ) % (NS_PR, REL_SLIDE_MASTER)


def build_pptx(content, title=None, template=None):
    """从大纲文本生成 pptx 字节流。

    template 不为空时，把新幻灯片追加到模板文件里（沿用模板的主题、母版、页面尺寸），
    这样学校统一模板的配色、校徽、页码都能保留。
    """
    slides = parse_slides(content)
    if not slides:
        slides = [{"title": title or "课件", "blocks": [{"type": "paragraph", "text": ""}], "notes": []}]
    if title and slides[0].get("title"):
        slides[0]["title"] = title

    if template and os.path.isfile(template):
        data = _append_to_template(template, slides)
    else:
        data = _build_new_pptx(slides)
    return data, slides


def _build_new_pptx(slides):
    parts = [
        ("[Content_Types].xml", _pptx_content_types(len(slides))),
        ("_rels/.rels", XML_HEAD + (
            '<Relationships xmlns="%s">'
            '<Relationship Id="rId1" Type="%s" Target="ppt/presentation.xml"/>'
            "</Relationships>" % (NS_PR, REL_OFFICE_DOCUMENT))),
        ("ppt/presentation.xml", _presentation_xml(len(slides))),
        ("ppt/_rels/presentation.xml.rels", _presentation_rels(len(slides))),
        ("ppt/slideMasters/slideMaster1.xml", _slide_master_xml()),
        ("ppt/slideMasters/_rels/slideMaster1.xml.rels", _slide_master_rels()),
        ("ppt/slideLayouts/slideLayout1.xml", _slide_layout_xml()),
        ("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _slide_layout_rels()),
        ("ppt/theme/theme1.xml", _theme_xml()),
        ("ppt/presProps.xml", XML_HEAD + '<p:presentationPr xmlns:a="%s" xmlns:r="%s" xmlns:p="%s"/>'
         % (NS_A, NS_R, NS_P)),
        ("ppt/viewProps.xml", XML_HEAD + (
            '<p:viewPr xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
            '<p:normalViewPr><p:restoredLeft sz="15620"/><p:restoredTop sz="94660"/></p:normalViewPr>'
            "<p:slideViewPr><p:cSldViewPr><p:cViewPr varScale=\"1\">"
            '<p:scale><a:sx n="100" d="100"/><a:sy n="100" d="100"/></p:scale>'
            '<p:origin x="0" y="0"/></p:cViewPr><p:guideLst/></p:cSldViewPr></p:slideViewPr>'
            '<p:gridSpacing cx="72008" cy="72008"/></p:viewPr>' % (NS_A, NS_R, NS_P))),
        ("ppt/tableStyles.xml", XML_HEAD + '<a:tblStyleLst xmlns:a="%s" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>' % NS_A),
    ]
    for index, slide in enumerate(slides, start=1):
        parts.append(("ppt/slides/slide%d.xml" % index, _slide_xml(slide, index)))
        parts.append(("ppt/slides/_rels/slide%d.xml.rels" % index, _slide_rels()))
    return _zip_bytes(parts)


def _pptx_content_types(count):
    overrides = [
        ("/ppt/presentation.xml", CT_PRESENTATION),
        ("/ppt/slideMasters/slideMaster1.xml", CT_SLIDE_MASTER),
        ("/ppt/slideLayouts/slideLayout1.xml", CT_SLIDE_LAYOUT),
        ("/ppt/theme/theme1.xml", CT_THEME),
        ("/ppt/presProps.xml", CT_PRES_PROPS),
        ("/ppt/viewProps.xml", CT_VIEW_PROPS),
        ("/ppt/tableStyles.xml", CT_TABLE_STYLES),
    ]
    overrides += [("/ppt/slides/slide%d.xml" % index, CT_SLIDE) for index in range(1, count + 1)]
    return XML_HEAD + (
        '<Types xmlns="%s">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>%s</Types>'
    ) % (NS_CT, "".join('<Override PartName="%s" ContentType="%s"/>' % item for item in overrides))


def _presentation_xml(count):
    # 关系编号必须和 _presentation_rels 完全一致：幻灯片 rId1..rIdN，母版 rId(N+1)
    sld_ids = "".join('<p:sldId id="%d" r:id="rId%d"/>' % (255 + index, index)
                      for index in range(1, count + 1))
    return XML_HEAD + (
        '<p:presentation xmlns:a="%s" xmlns:r="%s" xmlns:p="%s" saveSubsetFonts="1">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId%d"/></p:sldMasterIdLst>'
        "<p:sldIdLst>%s</p:sldIdLst>"
        '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    ) % (NS_A, NS_R, NS_P, count + 1, sld_ids, SLIDE_W, SLIDE_H)


def _presentation_rels(count):
    rels = ['<Relationship Id="rId%d" Type="%s" Target="slideMasters/slideMaster1.xml"/>'
            % (count + 1, REL_SLIDE_MASTER)]
    for index in range(1, count + 1):
        rels.append('<Relationship Id="rId%d" Type="%s" Target="slides/slide%d.xml"/>'
                    % (index, REL_SLIDE, index))
    rels.append('<Relationship Id="rId%d" Type="%s" Target="presProps.xml"/>' % (count + 2, REL_PRES_PROPS))
    rels.append('<Relationship Id="rId%d" Type="%s" Target="viewProps.xml"/>' % (count + 3, REL_VIEW_PROPS))
    rels.append('<Relationship Id="rId%d" Type="%s" Target="theme/theme1.xml"/>' % (count + 4, REL_THEME))
    rels.append('<Relationship Id="rId%d" Type="%s" Target="tableStyles.xml"/>' % (count + 5, REL_TABLE_STYLES))
    return XML_HEAD + '<Relationships xmlns="%s">%s</Relationships>' % (NS_PR, "".join(rels))


def _append_to_template(template, slides):
    """把幻灯片追加到已有模板：保留模板所有部件，只新增 slide 部件并登记关系。"""
    with zipfile.ZipFile(template) as archive:
        entries = [(item.filename, archive.read(item.filename))
                   for item in archive.infolist() if not item.filename.endswith("/")]
    parts = dict(entries)
    names = [name for name, _ in entries]

    existing_slides = sorted(
        [name for name in names if re.match(r"^ppt/slides/slide\d+\.xml$", name)],
        key=lambda name: int(re.findall(r"(\d+)", os.path.basename(name))[0]))
    next_index = (int(re.findall(r"(\d+)", os.path.basename(existing_slides[-1]))[0]) + 1
                  if existing_slides else 1)

    # 找模板里可用的版式（优先沿用最后一张幻灯片使用的版式）
    layout_part = "ppt/slideLayouts/slideLayout1.xml"
    if existing_slides:
        rels_name = "ppt/slides/_rels/%s.rels" % os.path.basename(existing_slides[-1])
        rels_text = parts.get(rels_name, b"").decode("utf-8", "replace")
        found = re.search(r'Target="\.\./(slideLayouts/[^"]+)"', rels_text)
        if found:
            layout_part = "ppt/" + found.group(1)
    if layout_part not in names and "ppt/slideLayouts/slideLayout1.xml" in names:
        layout_part = "ppt/slideLayouts/slideLayout1.xml"

    # presentation.xml：追加 sldId
    presentation = parts["ppt/presentation.xml"].decode("utf-8")
    rels = parts["ppt/_rels/presentation.xml.rels"].decode("utf-8")
    used_ids = [int(value) for value in re.findall(r'<p:sldId id="(\d+)"', presentation)]
    next_sld_id = max(used_ids + [255]) + 1
    used_rids = [int(value) for value in re.findall(r'Id="rId(\d+)"', rels)]
    next_rid = max(used_rids + [0]) + 1

    new_sld_ids = []
    new_rels = []
    new_overrides = []
    for offset, slide in enumerate(slides):
        index = next_index + offset
        part_name = "ppt/slides/slide%d.xml" % index
        parts[part_name] = _slide_xml(slide, index).encode("utf-8")
        relative_layout = layout_part[len("ppt/"):]
        parts["ppt/slides/_rels/slide%d.xml.rels" % index] = (XML_HEAD + (
            '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s" Target="../%s"/>'
            "</Relationships>" % (NS_PR, REL_SLIDE_LAYOUT, relative_layout))).encode("utf-8")
        new_sld_ids.append('<p:sldId id="%d" r:id="rId%d"/>' % (next_sld_id + offset, next_rid + offset))
        new_rels.append('<Relationship Id="rId%d" Type="%s" Target="slides/slide%d.xml"/>'
                        % (next_rid + offset, REL_SLIDE, index))
        new_overrides.append('<Override PartName="/%s" ContentType="%s"/>' % (part_name, CT_SLIDE))

    if "<p:sldIdLst>" in presentation:
        # 追加到已有幻灯片之后，保持模板原有页面顺序
        presentation = presentation.replace("</p:sldIdLst>",
                                            "".join(new_sld_ids) + "</p:sldIdLst>", 1)
    else:
        presentation = re.sub(r"(<p:sldMasterIdLst>.*?</p:sldMasterIdLst>)",
                              r"\1<p:sldIdLst>%s</p:sldIdLst>" % "".join(new_sld_ids),
                              presentation, count=1, flags=re.S)
    parts["ppt/presentation.xml"] = presentation.encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = rels.replace(
        "</Relationships>", "".join(new_rels) + "</Relationships>").encode("utf-8")

    content_types = parts["[Content_Types].xml"].decode("utf-8")
    parts["[Content_Types].xml"] = content_types.replace(
        "</Types>", "".join(new_overrides) + "</Types>").encode("utf-8")

    order = [name for name, _ in entries]
    for name in parts:
        if name not in order:
            order.append(name)
    return _zip_bytes([(name, parts[name]) for name in order])


def read_pptx(path):
    """读取 pptx，还原成与 build_pptx 相同的大纲格式（读旧稿 → 改 → 写新稿）。

    标题写成 `# 标题`，正文段落与项目符号写成 `- `（嵌套按缩进还原），
    表格写成 Markdown 表格。备注不在 pptx 里（生成时另存讲稿文件），因此不会被读回。
    """
    outline = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        names = sorted([name for name in archive.namelist()
                        if re.match(r"^ppt/slides/slide\d+\.xml$", name)],
                       key=lambda name: int(re.findall(r"(\d+)", os.path.basename(name))[0]))
        total = len(names)
        for name in names:
            with archive.open(name) as handle:
                root = ET.parse(handle).getroot()
            shapes = [node for node in root.iter() if _local(node.tag) == "sp"]
            tables = [node for node in root.iter() if _local(node.tag) == "tbl"]
            titles = [shape for shape in shapes if _shape_name(shape).lower().startswith("title")]
            bodies = [shape for shape in shapes if shape not in titles]
            if not titles and bodies:
                titles, bodies = [bodies[0]], bodies[1:]
            title = _shape_text(titles[0]).split("\n")[0] if titles else ""
            outline.append("# " + (title.strip() or "（无标题）"))
            for shape in bodies:
                for level, text, bulleted in _shape_paragraphs(shape):
                    if not text:
                        continue
                    marker = "" if level == 0 else "  " * level
                    prefix = "- " if bulleted else ""
                    outline.append("%s%s%s" % (marker, prefix, text))
            for table in tables:
                outline.extend(_table_lines(table))
            outline.append("")

    if not total:
        return "# 课件大纲（没有找到幻灯片）\n"
    return "\n".join(outline).strip() + "\n"


def _shape_name(shape):
    for node in shape.iter():
        if _local(node.tag) == "cNvPr":
            return node.get("name") or ""
    return ""


def _shape_paragraphs(shape):
    """返回 [(缩进级别, 文本, 是否项目符号)]。"""
    paragraphs = []
    for node in shape.iter():
        if _local(node.tag) != "p":
            continue
        level = 0
        bulleted = False
        for child in node:
            if _local(child.tag) != "pPr":
                continue
            try:
                level = int(child.get("lvl") or 0)
            except (TypeError, ValueError):
                level = 0
            for property_node in child:
                tag = _local(property_node.tag)
                if tag in ("buChar", "buAutoNum"):
                    bulleted = True
            break
        runs = [child.text for child in node.iter() if _local(child.tag) == "t" and child.text]
        text = "".join(runs).strip()
        if text:
            paragraphs.append((min(4, max(0, level)), text, bulleted))
    return paragraphs


def _shape_text(shape):
    return "\n".join(text for _level, text, _bulleted in _shape_paragraphs(shape))


def _table_lines(table):
    lines = []
    for row in table:
        if _local(row.tag) != "tr":
            continue
        cells = []
        for cell in row:
            if _local(cell.tag) != "tc":
                continue
            cells.append(" ".join(node.text for node in cell.iter()
                                  if _local(node.tag) == "t" and node.text).strip())
        if cells:
            lines.append("| " + " | ".join(cells) + " |")
    if lines and len(lines) > 0:
        lines.insert(1, "|" + "---|" * (lines[0].count("|") - 1))
    return lines


def slides_to_notes(slides, title=None):
    """把备注整理成讲稿 Markdown；没有备注返回空字符串。"""
    entries = [(index, slide) for index, slide in enumerate(slides, start=1) if slide.get("notes")]
    if not entries:
        return ""
    lines = ["# %s · 讲稿" % (title or "课件"), ""]
    for index, slide in entries:
        lines.append("## 第 %d 张：%s" % (index, slide.get("title") or ""))
        lines.append("")
        for note in slide["notes"]:
            lines.append(note)
        lines.append("")
    return "\n".join(lines).strip() + "\n"
