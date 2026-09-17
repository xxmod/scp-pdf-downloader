# -*- coding: utf-8 -*-
"""
LaTeX 源码导出模块
将抓取解析的数据转换为与 scp-pdf-master 完全兼容的 .tex 文件，
自动补全 part02/191.tex 到 200.tex 并更新 part02/index.tex。
"""

import os
import re
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup


class SCPTexExporter:
    def __init__(self, master_dir: Optional[str] = None):
        """
        初始化 LaTeX 导出器。
        优先寻找用户重新保存的 data/scp-pdf-master，若不存在则回退至当前目录的 scp-pdf-master。
        :param master_dir: scp-pdf-master 项目根目录（可选）
        """
        if master_dir:
            self.master_dir = os.path.abspath(master_dir)
        else:
            candidates = [
                os.path.join("data", "scp-pdf-master"),
                "scp-pdf-master"
            ]
            chosen = "scp-pdf-master"
            for c in candidates:
                if os.path.exists(c):
                    chosen = c
                    break
            self.master_dir = os.path.abspath(chosen)

        self.part00_dir = os.path.join(self.master_dir, "part00")
        self.part01_dir = os.path.join(self.master_dir, "part01")
        self.part02_dir = os.path.join(self.master_dir, "part02")
        self.images_dir = os.path.join(self.master_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)

    def _html_to_latex(self, html_text: str) -> str:
        """
        将经过清洗的 HTML 转换为 LaTeX 格式
        """
        soup = BeautifulSoup(html_text, "html.parser")
        lines = []

        for elem in soup.children:
            if not elem or (isinstance(elem, str) and not elem.strip()):
                continue

            tag_name = getattr(elem, "name", None)

            # 图片容器
            if tag_name == "div" and "scp-image-container" in elem.get("class", []):
                img_tag = elem.find("img")
                cap_tag = elem.find("div", class_="scp-caption")
                if img_tag and img_tag.get("src"):
                    src = img_tag["src"]
                    img_filename = os.path.basename(src)
                    caption = cap_tag.get_text().strip() if cap_tag else ""
                    lines.append("\\begin{figure}[H]")
                    lines.append("\t\\centering")
                    lines.append(f"\t\\includegraphics[width=0.6\\linewidth]{{images/{img_filename}}}")
                    if caption:
                        lines.append(f"\t\\caption*{{{caption}}}")
                    lines.append("\\end{figure}\n")
                continue

            # 文本框 (scpbox / blockquote)
            if tag_name in ["blockquote", "div"] and ("scpbox" in elem.get("class", []) or tag_name == "blockquote"):
                lines.append("\\begin{scpbox}")
                inner_text = elem.get_text().strip()
                lines.append(inner_text)
                lines.append("\\end{scpbox}\n")
                continue

            # 段落
            if tag_name == "p":
                p_html = str(elem)
                # 转换加粗
                p_text = re.sub(r"<strong>(.*?)</strong>", r"\\bb{\1}", p_html)
                p_text = re.sub(r"<b>(.*?)</b>", r"\\bb{\1}", p_text)
                p_text = re.sub(r"<[^>]+>", "", p_text).strip()
                if p_text:
                    lines.append(p_text + "\n")
                continue

            # 纯文本
            plain = elem.get_text().strip() if hasattr(elem, "get_text") else str(elem).strip()
            if plain:
                lines.append(plain + "\n")

        return "\n".join(lines)

    def export_item(self, item: Dict[str, Any]) -> str:
        """
        将单篇条目导出为标准 .tex 源码内容。
        """
        num = item["num"]
        slug_upper = item["slug"].upper()
        title_en = item.get("title_en", slug_upper)
        title_cn = item.get("title_cn", slug_upper)

        tex_parts = []
        tex_parts.append(f"\\chapter[{slug_upper} {title_cn}]{{")
        tex_parts.append(f"\t{title_en} \\\\")
        tex_parts.append(f"\t{slug_upper} {title_cn}")
        tex_parts.append("}\n")
        tex_parts.append(f"\\label{{chap:{slug_upper}}}\n")

        # 转换正文
        body_tex = self._html_to_latex(item["html_body"])
        tex_parts.append(body_tex)

        return "\n".join(tex_parts)

    def update_part02_index(self, start_num: int = 191, end_num: int = 200):
        """
        自动检查并向 part02/index.tex 追加新收录的篇目（如 191~200）。
        """
        index_file = os.path.join(self.part02_dir, "index.tex")
        if not os.path.exists(index_file):
            return

        with open(index_file, "r", encoding="utf-8") as f:
            content = f.read()

        added = []
        for n in range(start_num, end_num + 1):
            line = f"\\input{{part02/{n}}}"
            if line not in content:
                content += f"\n{line}"
                added.append(n)

        if added:
            with open(index_file, "w", encoding="utf-8") as f:
                f.write(content.strip() + "\n")
            print(f"[LaTeX源码] 已向 part02/index.tex 追加篇目: {added}")
