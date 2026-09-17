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
        # 直接从项目根目录读取 logo.png
        logo_path = os.path.join(self.work_dir, "./templates/logo.png")
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            # 写入缓存
            with open(cached_b64, "w", encoding="utf-8") as f:
                f.write(b64)
            return b64
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

        # 兜底防御：若存在 colmod-block 列表型折叠嵌套，展平其嵌套以杜绝任何 Chromium 栈溢出崩溃
        if "colmod-block" in html_content:
            from bs4 import BeautifulSoup
            clean_soup = BeautifulSoup(html_content, "html.parser")
            for colmod in list(clean_soup.find_all("div", class_="colmod-block")):
                for dummy in list(colmod.find_all("li", style=lambda s: s and "none" in s)):
                    dummy.decompose()
                link = colmod.find(class_="colmod-link-top")
                if link:
                    txt = link.get_text().strip()
                    half = len(txt) // 2
                    if half > 0 and txt[:half] == txt[half:]:
                        txt = txt[:half]
                    ptag = clean_soup.new_tag("p")
                    btag = clean_soup.new_tag("strong")
                    btag.string = f"[{txt}]"
                    ptag.append(btag)
                    link.replace_with(ptag)
                colmod.unwrap()
            for c in list(clean_soup.find_all("div", class_="colmod-content")):
                c.unwrap()
            for li in list(clean_soup.find_all("li", class_="folded")):
                li.unwrap()
            html_content = str(clean_soup)

        return html_content

    def _post_process_pdf(self, raw_pdf_path: str, final_pdf_path: str, items: List[Dict[str, Any]]):
        """
        使用 PyMuPDF 对 PDF 进行后处理：
        1. 定位目录页和各 SCP 章节在 PDF 中的实际页码；
        2. 封面与目录绝对不绘制页码；
        3. 正文页严格在安全区绘制页眉横线与页脚页码；
        4. 注入原生 PDF 书签大纲 (Bookmarks / TOC)。
        """
        print("\n[阶段 3/4] 启动 PyMuPDF 结构分析引擎，扫描文档各章节起始位置...", flush=True)
        doc = pymupdf.open(raw_pdf_path)
        total_pages = len(doc)
        print(f"  - 原始 PDF 总页数: {total_pages} 页，开始逐页分析标记...", flush=True)

        # 构建匹配字典（基于唯一的 marker，并预先计算无符号的标准化匹配键）
        marker_to_item = {item["marker"]: item for item in items}
        norm_to_item = {re.sub(r'[\s_\-]', '', item["marker"]): item for item in items}

        # 1. 扫描定位各条目真实正文起始页
        item_start_pages: Dict[str, int] = {}

        total_items_count = len(items)
        for pno in range(total_pages):
            page_idx = pno + 1
            raw_text = doc[pno].get_text()
            norm_text = re.sub(r'[\s_\-]', '', raw_text)

            # 寻找章节标记 marker（标准化匹配杜绝渲染空格/下划线差异）
            for norm_m, item_obj in norm_to_item.items():
                orig_m = item_obj["marker"]
                if norm_m in norm_text and orig_m not in item_start_pages:
                    item_start_pages[orig_m] = page_idx

            if page_idx % 250 == 0 or page_idx == total_pages:
                pct = page_idx / total_pages * 100
                print(f"  - [扫描章节起始页] 进度: {page_idx}/{total_pages} 页 ({pct:.1f}%) | 已定位 {len(item_start_pages)}/{total_items_count} 个章节", flush=True)

            # 优化早退：如果所有章节的起始位置均已定位，提前退出扫描，无需遍历后续上千页文本
            if len(item_start_pages) == total_items_count:
                print(f"  - [扫描章节起始页] 已提前定位全部 {total_items_count} 个章节（在第 {page_idx} 页提前完成扫描，节省后续遍历）", flush=True)
                break

        # 第 1 篇条目的起始页即为正文的真正起点
        min_start_p = min(item_start_pages.values()) if item_start_pages else 3
        first_scp_page = min_start_p
        total_toc_pages = first_scp_page - 2

        print(f"  - 章节结构解析完成: 目录页范围第 2 ~ {first_scp_page - 1} 页 (共 {total_toc_pages} 页)，正文第一页起始于总第 {first_scp_page} 页", flush=True)

        # 构建每一页所属的当前章节信息 (用于绘制页眉)
        sorted_items = sorted(
            [(marker_to_item[m], p) for m, p in item_start_pages.items()],
            key=lambda x: x[1]
        )
        page_to_current_scp: Dict[int, Dict[str, Any]] = {}

        for i, (item_obj, start_p) in enumerate(sorted_items):
            end_p = sorted_items[i + 1][1] if i + 1 < len(sorted_items) else (total_pages + 1)
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

        print(f"\n[阶段 4/4] 逐页绘制安全区页眉细线、动态章节标题及居中页数 (总计 {total_pages} 页)...", flush=True)

        # 预先初始化字体对象（全局复用）
        header_font = pymupdf.Font(fontfile=self.cjk_font_file) if self.cjk_font_file else None
        num_font = pymupdf.Font("helv")
        # 将 CJK 字体以子集方式预先嵌入到文档中，后续页面直接用 fontname 引用，
        # 避免每页都嵌入全量字体造成文件膨胀
        cjk_fontname_in_doc = None
        if self.cjk_font_file:
            try:
                # 在第一页嵌入一次，返回嵌入后的 fontname
                ref = doc[0].insert_text(
                    pymupdf.Point(-100, -100),  # 页面外不可见区域
                    "\u200b",  # 零宽空格，仅用于注册字体
                    fontsize=1,
                    fontname="cjk",
                    fontfile=self.cjk_font_file,
                    color=(1, 1, 1),  # 白色不可见
                )
                cjk_fontname_in_doc = "cjk"
            except Exception:
                cjk_fontname_in_doc = None

        for pno in range(total_pages):
            page_idx = pno + 1
            page = doc[pno]

            # 1. 封面 (第 1 页)：绝对不绘制页眉页脚
            if page_idx == 1:
                continue

            # 2. 目录页 (第 2 页至 first_scp_page - 1)：绝对不绘制页数
            if page_idx < first_scp_page:
                continue

            # 3. 正文页：精确绘制页眉横线、章节标题与页脚页数
            item_info = page_to_current_scp.get(page_idx)
            header_text = ""
            if item_info:
                if item_info.get("is_hub"):
                    header_text = "1. SCP-001 等待解密[已锁]"
                elif item_info.get("is_proposal"):
                    code_name = item_info.get("code_name", "SCP-001 提案")
                    title = item_info.get("title_cn", "")
                    if title and title != code_name:
                        header_text = f"001. {code_name} {title}"
                    else:
                        header_text = f"001. {code_name}"
                else:
                    header_text = f"{item_info['num']}. {item_info['slug'].upper()} {item_info['title_cn']}"

            # 绘制页眉细横线 (0.4pt，颜色 #999999)
            page.draw_line(
                pymupdf.Point(line_x0, header_line_y),
                pymupdf.Point(line_x1, header_line_y),
                color=(0.6, 0.6, 0.6),
                width=0.4
            )

            # 绘制页眉文字：偶数页居左，奇数页居右
            if header_text and header_font:
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
                    insert_kwargs = dict(
                        fontsize=header_fontsize,
                        color=(0.25, 0.25, 0.25)
                    )
                    if cjk_fontname_in_doc:
                        # 字体已嵌入，直接引用，不再重复传 fontfile
                        insert_kwargs["fontname"] = cjk_fontname_in_doc
                    elif self.cjk_font_file:
                        insert_kwargs["fontname"] = "cjk"
                        insert_kwargs["fontfile"] = self.cjk_font_file
                    page.insert_text(
                        pymupdf.Point(start_x, header_text_y),
                        header_text,
                        **insert_kwargs
                    )
                except Exception:
                    pass

            # 绘制底部居中页数 (从 1 开始累计)
            page_num_str = str(body_page_counter)
            body_page_counter += 1

            num_w = num_font.text_length(page_num_str, fontsize=9.5)
            center_x = (291.21 - num_w) / 2
            page.insert_text(
                pymupdf.Point(center_x, footer_text_y),
                page_num_str,
                fontsize=9.5,
                color=(0.1, 0.1, 0.1)
            )

            if page_idx % 200 == 0 or page_idx == total_pages:
                pct = page_idx / total_pages * 100
                cur_desc = header_text if header_text else ("封面" if page_idx == 1 else "目录")
                print(f"  - [绘制进度] 第 {page_idx}/{total_pages} 页 ({pct:.1f}%) | 正文页码: {body_page_counter - 1} | 章节: {cur_desc[:24]}", flush=True)

        # 4. 注入原生 PDF 书签大纲 (TOC Outline)
        print("  - [大纲构建] 正在生成原生 PDF 多级大纲书签树...", flush=True)
        toc = [
            [1, "封面", 1],
            [1, "目录", 2],
        ]

        hub_item = next((it for it in items if it.get("is_hub")), None)
        proposals = [it for it in items if it.get("is_proposal")]
        regular_items = [it for it in items if not it.get("is_hub") and not it.get("is_proposal")]

        if hub_item or proposals:
            p_hub = item_start_pages.get(hub_item["marker"], first_scp_page) if hub_item else first_scp_page
            # 一级大纲：SCP-001 提案
            toc.append([1, "SCP-001 提案", p_hub])
            if hub_item:
                toc.append([2, "SCP-001 等待解密 [已锁]", p_hub])
            for prop in proposals:
                p_prop = item_start_pages.get(prop["marker"], first_scp_page)
                code_name = prop.get("code_name", "SCP-001 提案")
                title = prop.get("title_cn", "")
                b_title = f"{code_name} - {title}" if (title and title != code_name) else f"{code_name}"
                toc.append([2, b_title, p_prop])

        for item in regular_items:
            slug_up = item["slug"].upper()
            title = item["title_cn"]
            p_target = item_start_pages.get(item["marker"], first_scp_page)
            bookmark_title = f"{slug_up} {title}"
            toc.append([1, bookmark_title, p_target])

        print(f"  - [大纲构建] 成功生成 {len(toc)} 条大纲节点，正在注入 PDF 文件...", flush=True)
        doc.set_toc(toc)
        print(f"  - [保存输出] 正在将全部修改持久化写入: {final_pdf_path}...", flush=True)
        doc.save(
            final_pdf_path,
            garbage=4,          # 删除冠余对象并嵌入字体子集
            deflate=True,       # 压缩所有流
            deflate_images=True # 压缩图像流
        )
        doc.close()
        print(f"[PDF后处理完成] 成功写入多级大纲与修正页码，最终输出: {final_pdf_path}", flush=True)

    def build_pdf(self, items: List[Dict[str, Any]], output_pdf_path: str, version: str = "1.20") -> str:
        """
        生成最终的高保真 PDF 文件。
        """
        abs_output_path = os.path.abspath(output_pdf_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)

        base_stem = os.path.splitext(os.path.basename(output_pdf_path))[0]
        temp_html_path = os.path.join(self.work_dir, "data", f"{base_stem}_temp.html")
        raw_pdf_path = os.path.join(self.work_dir, "data", f"raw_{base_stem}.pdf")

        print(f"\n[阶段 1/4] 正在根据 Jinja2 模板渲染包含 {len(items)} 个条目的完整书籍 HTML...", flush=True)
        rendered_html = self.render_html(items, version=version)

        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)
        html_mb = os.path.getsize(temp_html_path) / (1024 * 1024)
        print(f"  - HTML 渲染写入完成: {temp_html_path} ({html_mb:.2f} MB)", flush=True)

        print("\n[阶段 2/4] 启动 Playwright Chromium 无头浏览器进行核心版心排版...", flush=True)
        with sync_playwright() as p:
            launch_args = [
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-first-run",
                "--safebrowsing-disable-auto-update"
            ]
            browser = p.chromium.launch(args=launch_args)
            page = browser.new_page()
            
            file_url = f"file:///{temp_html_path.replace(os.sep, '/')}"
            print(f"  - 正在加载本地文档 DOM 与全部图像资源: {file_url[:50]}...", flush=True)
            page.goto(file_url, wait_until="load", timeout=120000)
            print(f"  - 页面 DOM 与样式加载完成，开始执行全书分页排版（共收录 {len(items)} 篇文档，排版耗时与文档篇幅相关，请稍候）...", flush=True)

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

        raw_doc = pymupdf.open(raw_pdf_path)
        actual_total_pages = len(raw_doc)
        raw_doc.close()
        raw_mb = os.path.getsize(raw_pdf_path) / (1024 * 1024)
        print(f"  - Chromium 版心排版渲染成功完成！实际总页数: {actual_total_pages} 页，原始 PDF 大小: {raw_mb:.2f} MB", flush=True)

        # 使用 PyMuPDF 进行后处理
        self._post_process_pdf(raw_pdf_path, abs_output_path, items)

        return abs_output_path
