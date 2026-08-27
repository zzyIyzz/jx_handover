"""Word 渲染：docxtpl 生成内容，python-docx 程序化着色与样式。

颜色规则必须是代码，不由 AI 或人工排版决定（色值取自参考模板实测）：
  专业工作：紧急=红底白字(FFA5A5)、重点=黄底黑字(FFFE83)、其余=白
  定期工作：已完成=绿底(C6EFCE)、期限内未完成=白、超期未完成=红底(FFA5A5)
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docxtpl import DocxTemplate

# 模板中的表格顺序（与 make_template.py 保持一致）：
# 0 基本信息 | 1 重点工作 | 2 需交接工作 | 3 外委考核
# | 4 月度定期 | 5 季度定期 | 6 年度定期
TABLE_IMPORTANT = 1
TABLE_HANDOVER = 2
TABLE_MONTHLY = 4
TABLE_QUARTERLY = 5
TABLE_YEARLY = 6

# 参考 docx 实测色值
ROW_COLORS = {
    "red": "FFA5A5",     # 紧急 / 超期未完成
    "yellow": "FFFE83",  # 重点
    "green": "C6EFCE",   # 定期工作已完成
    "white": None,       # 白色不着色
}
# 行底色 -> 字体颜色（红底白字、其余黑字）
FONT_COLORS = {"red": RGBColor(0xFF, 0xFF, 0xFF)}


def _shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)


def _set_row_font_color(row, color: RGBColor) -> None:
    for cell in row.cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.font.color.rgb = color


def _shade_table_rows(doc: Document, table_idx: int, colors: list[str]) -> None:
    if not colors:
        return
    table = doc.tables[table_idx]
    data_rows = table.rows[1:]  # 跳过表头
    for row, color in zip(data_rows, colors):
        fill = ROW_COLORS.get(color)
        if fill is None:
            continue
        for cell in row.cells:
            _shade_cell(cell, fill)
        font_color = FONT_COLORS.get(color)
        if font_color is not None:
            _set_row_font_color(row, font_color)


def render_word(template_path: Path, context: dict, colors: dict,
                output_path: Path) -> Path:
    tpl = DocxTemplate(str(template_path))
    tpl.render(context)
    tpl.save(str(output_path))

    # 后处理：程序着色
    doc = Document(str(output_path))
    _shade_table_rows(doc, TABLE_IMPORTANT, colors.get("important", []))
    _shade_table_rows(doc, TABLE_HANDOVER, colors.get("handover", []))
    _shade_table_rows(doc, TABLE_MONTHLY, colors.get("monthly", []))
    _shade_table_rows(doc, TABLE_QUARTERLY, colors.get("quarterly", []))
    _shade_table_rows(doc, TABLE_YEARLY, colors.get("yearly", []))
    doc.save(str(output_path))
    return output_path
