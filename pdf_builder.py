# -*- coding: utf-8 -*-
"""
高保真 PDF 电子书构建器
采用 Playwright 无头 Chromium 内核，严格按照 OnePlus 3 (op3) 规格渲染导出整合 PDF。
"""

import os
import random
import datetime
import base64
from typing import List, Dict, Any
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright


class SCPPdfBuilder:
    def __init__(self, template_dir: str = "templates"):
        """
        初始化 PDF 构建器。
        """
        self.work_dir = os.path.abspath(os.path.dirname(__file__))
        self.template_dir = os.path.join(self.work_dir, template_dir)
        self.env = Environment(loader=FileSystemLoader(self.work_dir))

    def _get_logo_base64(self) -> str:
        """
        获取 SCP 官方 Logo 的 base64 字符串
        """
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

    def build_pdf(self, items: List[Dict[str, Any]], output_pdf_path: str, version: str = "1.20") -> str:
        """
        生成最终的高保真 PDF 文件。
        :param items: 条目数据列表
        :param output_pdf_path: 目标 PDF 文件路径
        :param version: 书籍版本号
        :return: 最终 PDF 的绝对路径
        """
        abs_output_path = os.path.abspath(output_pdf_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)

        print(f"[PDF构建] 正在渲染包含 {len(items)} 个条目的书籍 HTML...")
        rendered_html = self.render_html(items, version=version)

        # 写入临时 HTML 文件，方便 Chromium 加载本地图片与 CSS
        temp_html_path = os.path.join(self.work_dir, "data", "book_render_temp.html")
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)

        print("[PDF构建] 启动 Chromium 引擎进行高保真排版并输出 PDF...")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # 使用 file:// 协议打开渲染文件
            file_url = f"file:///{temp_html_path.replace(os.sep, '/')}"
            page.goto(file_url, wait_until="networkidle")

            # 页眉模板：右上角手册信息，底端带灰色细横线
            header_template = (
                '<div style="width:100%; font-size:6.5pt; font-family:\'Noto Sans SC\', sans-serif; color:#666; '
                'border-bottom:0.4pt solid #999; padding-bottom:3px; margin:0 0.45in; '
                'display:flex; justify-content:space-between; align-items:flex-end;">'
                '<span>SCP 基金会内部人员参考手册</span>'
                '<span>机密 / 仅限内阅</span>'
                '</div>'
            )

            # 页脚模板：中央标准阿拉伯数字页码
            footer_template = (
                '<div style="width:100%; font-size:7.5pt; font-family:\'Noto Sans SC\', sans-serif; color:#222; '
                'text-align:center; margin:0 0.45in;">'
                '<span class="pageNumber"></span>'
                '</div>'
            )

            # 导出 OnePlus 3 移动版规格的 PDF (宽 4.0446in, 高 7.19055in)
            page.pdf(
                path=abs_output_path,
                width="4.0446in",
                height="7.19055in",
                margin={
                    "top": "0.85in",
                    "bottom": "0.55in",
                    "left": "0.45in",
                    "right": "0.45in"
                },
                print_background=True,
                display_header_footer=True,
                header_template=header_template,
                footer_template=footer_template,
                prefer_css_page_size=True
            )

            browser.close()

        print(f"[PDF构建完成] 成功生成文件: {abs_output_path}")
        return abs_output_path
