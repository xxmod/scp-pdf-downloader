# -*- coding: utf-8 -*-
"""
高保真 PDF 电子书构建器
1. 采用 Playwright 引擎进行精准版心排版 (严格遵循 OnePlus 3 比例与 8.97pt 字号规范)
2. 结合 PyMuPDF 进行精准后处理：
   - 封面与目录绝对不显示页码；
   - 正文安全区外精确绘制页眉横线与章节标，底部中央绘制页码，100% 杜绝内容覆盖页眉页脚；
   - 注入原生 PDF 书签大纲树 (Bookmarks / TOC)，记录每一个 SCP 名称对应的实际页数。
"""

import os
import re
import random
import datetime
import base64
from typing import List, Dict, Any, Tuple, Optional
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
import pymupdf


class SCPPdfBuilder:
    def __init__(self, template_dir: str = "templates"):
        """
        初始化 PDF 构建器。
        """
        self.work_dir = os.path.abspath(os.path.dirname(__file__))
        self.template_dir = os.path.join(self.work_dir, template_dir)
        self.env = Environment(loader=FileSystemLoader(self.work_dir))

        # 寻找可用的中文字体用于 PyMuPDF 插入文字
        self.cjk_font_file = self._detect_cjk_font()

    def _detect_cjk_font(self) -> Optional[str]:
        """
        检测系统可用的优质中文字体文件
        """
        candidates = [
            r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑
            r"C:\Windows\Fonts\simhei.ttf",   # 黑体
            r"C:\Windows\Fonts\simsun.ttc",   # 宋体
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def _get_logo_base64(self) -> str:
        """
        获取 SCP 官方 Logo 的 base64 字符串
        """
        cached_b64 = os.path.join(self.work_dir, "data", "logo_b64.txt")
        if os.path.exists(cached_b64):
            with open(cached_b64, "r", encoding="utf-8") as f:
                return f.read().strip()
        logo_path = os.path.join(self.work_dir, "scp-pdf-master", "images", "logo.png")
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        return ""

    def render_html(self, items: List[Dict[str, Any]], version: str = "1.20") -> str:
        """
        使用 Jinja2 模板将结构化条目渲染为完整的 HTML 字符串。
        """
        template = self.env.get_template("templates/op3_book.html")
        logo_b64 = self._get_logo_base64()
        stamp_code = str(random.randint(10000000, 99999999))
        now_str = datetime.datetime.now().strftime("%Y.%m.%d")

        # 读取 CSS 文件内容直接内联
        css_path = os.path.join(self.work_dir, "templates", "op3_style.css")
        inline_css = ""
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as cf:
                inline_css = cf.read()

        html_content = template.render(
            book_title="SCP 基金会内部人员手册",
            logo_base64=logo_b64,
            random_stamp_code=stamp_code,
            version=version,
            date_str=now_str,
            items=items,
            inline_css=inline_css
        )
        return html_content

    def _post_process_pdf(self, raw_pdf_path: str, final_pdf_path: str, items: List[Dict[str, Any]]):
        """
        使用 PyMuPDF 对 PDF 进行后处理：
        1. 定位目录页和各 SCP 章节在 PDF 中的实际页码；
        2. 封面与目录绝对不绘制页码；
        3. 正文页严格在安全区绘制页眉横线与页脚页码；
        4. 注入原生 PDF 书签大纲 (Bookmarks / TOC)。
        """
        print("[PDF后处理] 正在分析页面结构并注入大纲与精准页眉页脚...")
        doc = pymupdf.open(raw_pdf_path)
        total_pages = len(doc)

        # 构建匹配字典
        slug_to_item = {item["slug"].lower(): item for item in items}
        num_to_item = {item["num"]: item for item in items}

        # 1. 扫描定位各条目真实正文起始页
        scp_start_pages: Dict[int, int] = {}

        for pno in range(total_pages):
            page_idx = pno + 1
            raw_text = doc[pno].get_text()

            # 寻找章节标记 #SCPMARK{num}#
            for item in items:
                num = item["num"]
                marker = f"#SCPMARK{num}#"
                if marker in raw_text and num not in scp_start_pages:
                    scp_start_pages[num] = page_idx

        # 第 1 篇条目的起始页即为正文的真正起点
        min_start_p = min(scp_start_pages.values()) if scp_start_pages else 3
        first_scp_page = min_start_p
        total_toc_pages = first_scp_page - 2

        print(f"  - 目录页范围: 第 2 ~ {first_scp_page - 1} 页 (共 {total_toc_pages} 页)")
        print(f"  - 正文起始于第 {first_scp_page} 页，成功精确定位 {len(scp_start_pages)} 个 SCP 条目的正文实际页码")

        # 构建每一页所属的当前章节信息 (用于绘制页眉)
        sorted_scps = sorted(scp_start_pages.items(), key=lambda x: x[1])
        page_to_current_scp: Dict[int, Dict[str, Any]] = {}

        for i, (num, start_p) in enumerate(sorted_scps):
            end_p = sorted_scps[i + 1][1] if i + 1 < len(sorted_scps) else (total_pages + 1)
            item_obj = num_to_item.get(num)
            if item_obj:
                for p in range(start_p, end_p):
                    page_to_current_scp[p] = item_obj

        # 开始逐页修饰与绘制
        header_line_y = 26.4       # 严格匹配原版绘图坐标
        header_text_y = 22.0
        footer_text_y = 502.0
        line_x0 = 14.17            # 0.5 cm
        line_x1 = 277.04           # 291.21 - 14.17

        # 正文页码计数从 1 开始
        body_page_counter = 1

        for pno in range(total_pages):
            page_idx = pno + 1
            page = doc[pno]

            # 1. 封面 (第 1 页)：绝对不绘制页眉页脚
            if page_idx == 1:
                continue

            # 2. 目录页 (第 2 页至 first_scp_page - 1)：绝对不绘制页数
            if page_idx < first_scp_page:
                # 顶部可选绘制“目录”横线或保持清爽，用户要求“封面与目录不应该有页数”
                continue

            # 3. 正文页：精确绘制页眉横线、章节标题与页脚页数
            item_info = page_to_current_scp.get(page_idx)
            header_text = ""
            if item_info:
                header_text = f"{item_info['num']}. {item_info['slug'].upper()} {item_info['title_cn']}"

            # 绘制页眉细横线 (0.4pt，颜色 #999999)
            page.draw_line(
                pymupdf.Point(line_x0, header_line_y),
                pymupdf.Point(line_x1, header_line_y),
                color=(0.6, 0.6, 0.6),
                width=0.4
            )

            # 绘制页眉文字：偶数页居左，奇数页居右
            if header_text and self.cjk_font_file:
                header_font = pymupdf.Font(fontfile=self.cjk_font_file)
                header_fontsize = 9.0
                
                # 若文字过长则截断
                if len(header_text) > 24:
                    header_text = header_text[:22] + "..."

                text_w = header_font.text_length(header_text, fontsize=header_fontsize)
                # 奇数页居右：文字最右侧分毫不差贴紧 line_x1
                if page_idx % 2 == 1:
                    start_x = max(line_x0, line_x1 - text_w)
                else: # 偶数页居左：贴紧 line_x0
                    start_x = line_x0

                try:
                    page.insert_text(
                        pymupdf.Point(start_x, header_text_y),
                        header_text,
                        fontsize=header_fontsize,
                        fontname="cjk",
                        fontfile=self.cjk_font_file,
                        color=(0.25, 0.25, 0.25)
                    )
                except Exception:
                    pass

            # 绘制底部居中页数 (从 1 开始累计)
            page_num_str = str(body_page_counter)
            body_page_counter += 1

            num_font = pymupdf.Font("helv")
            num_w = num_font.text_length(page_num_str, fontsize=9.5)
            center_x = (291.21 - num_w) / 2
            page.insert_text(
                pymupdf.Point(center_x, footer_text_y),
                page_num_str,
                fontsize=9.5,
                color=(0.1, 0.1, 0.1)
            )

        # 4. 注入原生 PDF 书签大纲 (TOC Outline)
        print("[PDF大纲] 正在生成原生 PDF 大纲书签树...")
        toc = [
            [1, "封面", 1],
            [1, "目录", 2],
        ]
        for item in items:
            num = item["num"]
            slug_up = item["slug"].upper()
            title = item["title_cn"]
            p_target = scp_start_pages.get(num, first_scp_page)
            # 大纲标题格式: SCP-002 “生活”室
            bookmark_title = f"{slug_up} {title}"
            toc.append([1, bookmark_title, p_target])

        doc.set_toc(toc)
        doc.save(final_pdf_path)
        doc.close()
        print(f"[PDF后处理完成] 成功写入大纲与修正页码，最终输出: {final_pdf_path}")

    def build_pdf(self, items: List[Dict[str, Any]], output_pdf_path: str, version: str = "1.20") -> str:
        """
        生成最终的高保真 PDF 文件。
        """
        abs_output_path = os.path.abspath(output_pdf_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)

        print(f"[PDF构建] 正在渲染包含 {len(items)} 个条目的书籍 HTML...")
        rendered_html = self.render_html(items, version=version)

        temp_html_path = os.path.join(self.work_dir, "data", "book_render_temp.html")
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)

        raw_pdf_path = os.path.join(self.work_dir, "data", "raw_rendered.pdf")

        print("[PDF构建] 启动 Chromium 引擎进行版心排版...")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            
            file_url = f"file:///{temp_html_path.replace(os.sep, '/')}"
            page.goto(file_url, wait_until="networkidle")

            # 导出纯净版心的 PDF (关闭浏览器自带的 header/footer，边距严格匹配原版 0.5cm)
            page.pdf(
                path=raw_pdf_path,
                width="4.0446in",
                height="7.19055in",
                margin={
                    "top": "0.5in",      # 36pt 留出顶部安全区
                    "bottom": "0.42in",  # 30pt 留出底部安全区
                    "left": "0.5cm",     # 14.17pt 原版左边距
                    "right": "0.5cm"     # 14.17pt 原版右边距
                },
                print_background=True,
                display_header_footer=False, # 禁用浏览器自带粗糙页眉页脚，改由 PyMuPDF 完美加盖
                prefer_css_page_size=True
            )

            browser.close()

        # 使用 PyMuPDF 进行后处理
        self._post_process_pdf(raw_pdf_path, abs_output_path, items)

        return abs_output_path
