# -*- coding: utf-8 -*-
"""
SCP 页面内容清洗与结构化解析模块
负责将抓取的原始 HTML 页面解析为规范的条目数据（标题、等级、收容措施、描述、插图、引用框等），
支持常规 SCP 条目（SCP-002~200）、SCP-001 枢纽页以及全部 SCP-001 提案文档。
"""

import os
import re
from bs4 import BeautifulSoup, Tag
from typing import Dict, Any, List, Optional, Tuple
from crawler import SCPCrawler


class SCPParser:
    def __init__(self, crawler: Optional[SCPCrawler] = None):
        """
        初始化 SCP 解析器。
        :param crawler: SCPCrawler 实例（用于辅助下载页面中引用的图片）
        """
        self.crawler = crawler or SCPCrawler()

    def _clean_content_div(self, content_div: Tag, soup: BeautifulSoup, slug: str, context: str = "", page_url: str = "") -> Tuple[str, List[Dict[str, Any]]]:
        """
        核心正文清洗流水线：
        1. 清除评级条、脚本、作者推广框、导航条等噪点标签；
        2. 清除繁体重复标签 (.lang-tr) 杜绝重复表头；
        3. 下载并替换插图，全图压缩为 quality=60，自动销毁失效或破损图片标签；
        4. 展开折叠框，标准化引用框为 .scpbox；
        5. 清除写死的超宽内联样式与浮动，杜绝页面溢出；
        6. 规范化常见标签加粗。
        """
        # 1. 移除无关组件与噪点标签
        unwanted_selectors = [
            ".page-rate-widget-box",
            ".rate-box-with-credit-styling",
            ".creditRate",
            ".creditButtonStandalone",
            "#u-credit-view",
            ".footer-wikiwalk-nav",
            ".license-box",
            ".page-tags",
            "#action-area",
            "script",
            "style",
            "noscript",
            ".comments-box",
            "#comments-content",
            ".options-title",
            "#page-version-info",
            ".authorlink-wrapper",
            ".authorbox",
            ".authorcontent",
            ".info-container",
            ".heritage-rating-module",
            ".modal-wrapper",
            ".modal",
            "#toc",
            ".classified-bar",
            ".class1image",
            ".image-space"
        ]
        for sel in unwanted_selectors:
            for tag in content_div.select(sel):
                tag.decompose()

        # 移除 ACS 异常分类条及文档中的繁体中文标签，消除“项目编号：項目編號：”双语重复
        for tr_tag in content_div.find_all(class_="lang-tr"):
            tr_tag.decompose()

        # 移除正文中散落的作者推广文案（如“喜欢这篇skip？”）
        for promo in content_div.find_all(["div", "p"]):
            promo_text = promo.get_text()
            if "喜欢这篇skip" in promo_text or "该作者的更多作品" in promo_text:
                promo.decompose()

        # 2. 提取与处理图片块 (div.scp-image-block)
        images = []
        img_blocks = content_div.find_all("div", class_="scp-image-block")
        for idx, block in enumerate(img_blocks, start=1):
            img_tag = block.find("img")
            if not img_tag or not img_tag.get("src"):
                block.decompose()
                continue

            src = img_tag["src"]
            caption_tag = block.find("div", class_="scp-image-caption")
            caption_text = caption_tag.get_text().strip() if caption_tag else ""

            # 过滤 ACS 权限装饰条图片
            if "classified-bar" in src or "classified-bar" in img_tag.get("alt", ""):
                block.decompose()
                continue

            img_ext = os.path.splitext(src.split("?")[0])[1] or ".jpg"
            save_name = f"{slug.upper()}_{idx}{img_ext}" if idx > 1 else f"{slug.upper()}{img_ext}"
            local_img_path = self.crawler.download_image(src, save_name, context=context, page_url=page_url)

            if local_img_path and os.path.exists(local_img_path):
                abs_img_uri = f"file:///{local_img_path.replace(os.sep, '/')}"
                new_container = soup.new_tag("div", **{"class": "scp-image-container"})
                new_img = soup.new_tag("img", **{"class": "scp-image", "src": abs_img_uri})
                new_container.append(new_img)
                if caption_text:
                    new_caption = soup.new_tag("div", **{"class": "scp-caption"})
                    new_caption.string = caption_text
                    new_container.append(new_caption)
                block.replace_with(new_container)
                images.append({"path": local_img_path, "caption": caption_text})
            else:
                block.decompose()

        # 2.2 处理表格、独立 div 等非标准位置的全部散落 img 标签，彻底避免破图图标
        for extra_idx, extra_img in enumerate(content_div.find_all("img"), start=1):
            src = extra_img.get("src", "")
            if not src or src.startswith("file:///"):
                continue

            # 过滤掉无关用户头像、微小图标，以及导致巨幅黑块的 ACS 权限条组件图片
            if any(k in src for k in ["avatar.php", "userkarma.php", "favicon", "local--favicon", "default.png", "classified-bar"]) or "classified-bar" in extra_img.get("alt", ""):
                extra_img.decompose()
                continue

            # 下载非标准插图（支持 wdfiles、表格插图、警告框Logo等）
            raw_fname = os.path.basename(src.split("?")[0])
            if not raw_fname or len(raw_fname) > 50:
                raw_fname = f"extra_{extra_idx}.jpg"
            save_fname = f"{slug.upper()}_{raw_fname}"

            local_img_path = self.crawler.download_image(src, save_fname, context=context, page_url=page_url)
            if local_img_path and os.path.exists(local_img_path):
                abs_img_uri = f"file:///{local_img_path.replace(os.sep, '/')}"
                extra_img["src"] = abs_img_uri
                extra_img["style"] = (extra_img.get("style", "") + "; max-width: 100%; height: auto;").strip("; ")
                images.append({"path": local_img_path, "caption": ""})
            else:
                # 严密防线：如果下载失败或本地不存在，直接销毁该 img 标签，决不在 PDF 中留下破图图标
                extra_img.decompose()

        # 3. 深度清洗授权/引用块：移除图像版权信息（文件名、图像作者等）及授权指南，只保留正文主引用
        for lic in content_div.find_all(lambda tag: tag.name == "div" and ("licensebox" in tag.get("class", []) or "授权" in tag.get_text())):
            for bq in list(lic.find_all("blockquote")):
                bq_text = bq.get_text()
                if any(k in bq_text for k in ["文件名：", "图像作者", "图像名称", "图像名：", "授权协议：", "派生于"]):
                    bq.decompose()
            for p in list(lic.find_all(["p", "a"])):
                if p.decomposed:
                    continue
                if "授权指南" in p.get_text() or "licensing-guide" in str(p.get("href", "")):
                    parent_p = p.find_parent("p") if p.name == "a" else p
                    if parent_p:
                        parent_p.decompose()

        for bq in list(content_div.find_all("blockquote")):
            bq_text = bq.get_text()
            if "文件名：" in bq_text and "图像作者" in bq_text:
                bq.decompose()

        for fold in content_div.find_all("div", class_="collapsible-block"):
            unfolded = fold.find("div", class_="collapsible-block-content")
            if unfolded:
                box_div = soup.new_tag("div", **{"class": "scpbox"})
                fold_title = fold.find("a", class_="collapsible-block-link")
                if fold_title:
                    title_p = soup.new_tag("p")
                    title_b = soup.new_tag("strong")
                    title_b.string = f"[{fold_title.get_text().strip()}]"
                    title_p.append(title_b)
                    box_div.append(title_p)
                for child in list(unfolded.children):
                    box_div.append(child)
                fold.replace_with(box_div)
            else:
                fold.decompose()

        # 4.1 处理多层列表型折叠模块 (colmod-block / foldable-list)
        # 针对如 SCP-5764 等长达上百层的递归折叠列表，展平其嵌套深度，杜绝 Chromium 渲染引擎发生 C++ 栈溢出崩溃
        for colmod in list(content_div.find_all("div", class_="colmod-block")):
            for dummy_li in list(colmod.find_all("li", style=lambda s: s and "none" in s)):
                dummy_li.decompose()
            link_top = colmod.find(class_="colmod-link-top")
            if link_top:
                t_text = link_top.get_text().strip()
                if t_text:
                    half = len(t_text) // 2
                    if half > 0 and t_text[:half] == t_text[half:]:
                        t_text = t_text[:half]
                    title_tag = soup.new_tag("p")
                    title_b = soup.new_tag("strong")
                    title_b.string = f"[{t_text}]"
                    title_tag.append(title_b)
                    link_top.replace_with(title_tag)
                else:
                    link_top.decompose()
            colmod.unwrap()

        for c in list(content_div.find_all("div", class_="colmod-content")):
            c.unwrap()
        for li in list(content_div.find_all("li", class_="folded")):
            li.unwrap()

        # 4.2 清理 iframe 与 script 标签（如 interwiki 侧栏 theme 样式帧及前端脚本，打印版无意义且可能导致崩溃或卡顿）
        for el in list(content_div.find_all(["iframe", "script"])):
            el.decompose()

        # 5. 标准化 blockquote 为 scpbox
        for bq in content_div.find_all("blockquote"):
            bq["class"] = bq.get("class", []) + ["scpbox"]

        # 5.1 针对提案《进程》等具有深绿边框的诊断框，将深黑填充背景改为白色，杜绝黑底黑字无法阅读
        for box in content_div.find_all(lambda t: t.has_attr("style") and "#247040" in t["style"]):
            st = box["style"]
            st = re.sub(r"background(-color)?\s*:\s*(#1F1C1E|#141414|#000000|#111111|black|rgba?\([^)]+\))", r"background\1: #ffffff", st, flags=re.IGNORECASE)
            box["style"] = st

        # 6. 清理可能写死大宽度的内联样式与浮动，杜绝横向溢出与内容重叠
        for tag in content_div.find_all(lambda t: t.has_attr("style")):
            st = tag["style"]
            if re.search(r"width:\s*\d{3,}px", st):
                tag["style"] = re.sub(r"width:\s*\d{3,}px", "max-width: 100%", st)
            if "margin" in tag["style"] and re.search(r"margin:\s*[^;]*\d{2,}px", tag["style"]):
                tag["style"] = re.sub(r"margin:\s*[^;]+;", "margin: 8pt auto;", tag["style"])
            if "float:" in tag["style"]:
                tag["style"] = re.sub(r"float:\s*[^;]+;", "float: none; margin: 6pt auto;", tag["style"])

        # 7. 关键标签加粗规范化（项目编号、项目等级、特殊收容措施、描述、附录）
        body_html = str(content_div)
        labels = ["项目编号", "项目等级", "特殊收容措施", "描述", "附录"]
        for lbl in labels:
            body_html = re.sub(rf"<strong>\s*({lbl}\s*[:：])\s*</strong>", r"\1", body_html)
            body_html = re.sub(rf"<b>\s*({lbl}\s*[:：])\s*</b>", r"\1", body_html)
            body_html = re.sub(rf"(?<!<strong>)({lbl}\s*[:：])", r"<strong>\1</strong>", body_html)

        # 重新包装
        body_soup = BeautifulSoup(body_html, "html.parser")
        final_div = body_soup.find("div", id="page-content")
        inner_html = "".join(str(c) for c in final_div.children) if final_div else body_html

        return inner_html, images

    def parse(self, scp_num: int, html_text: str, custom_title: str = "") -> Dict[str, Any]:
        """
        解析单个常规 SCP 页面的 HTML (SCP-002 ~ SCP-200)。
        :param scp_num: 项目数字，如 2
        :param html_text: 原始网页 HTML
        :param custom_title: 从系列索引获取的官方中文名（如 “生活”室）
        :return: 结构化条目字典
        """
        slug = self.crawler.get_slug(scp_num)
        soup = BeautifulSoup(html_text, "html.parser")
        content_div = soup.find("div", id="page-content")

        if not content_div:
            return {
                "num": scp_num,
                "slug": slug,
                "marker": f"#SCPMARK{scp_num}#",
                "title_en": f"{slug.upper()}",
                "title_cn": custom_title or f"{slug.upper()}",
                "code_name": "",
                "is_hub": False,
                "is_proposal": False,
                "html_body": "<p>[页面无有效正文内容]</p>",
                "images": []
            }

        title_cn = custom_title or f"{slug.upper()}"
        title_en = f"{slug.upper()}"
        context_name = f"{slug.upper()} - {custom_title}" if custom_title else slug.upper()

        inner_html, images = self._clean_content_div(content_div, soup, slug, context=context_name)

        return {
            "num": scp_num,
            "slug": slug,
            "marker": f"#SCPMARK{scp_num}#",
            "title_en": title_en,
            "title_cn": title_cn,
            "code_name": "",
            "is_hub": False,
            "is_proposal": False,
            "html_body": inner_html,
            "images": images
        }

    def parse_scp001_hub(self, html_text: str) -> Dict[str, Any]:
        """
        解析 SCP-001 枢纽页面（包含最高机密告示、通用说明001-Alpha、模因抹杀警告、高清分形大图及提案列表索引）。
        """
        slug = "scp-001"
        soup = BeautifulSoup(html_text, "html.parser")
        content_div = soup.find("div", id="page-content")

        if not content_div:
            return {
                "num": 1,
                "slug": slug,
                "marker": "#SCPMARK001HUB#",
                "title_en": "SCP-001 Awaiting De-classification [Blocked]",
                "title_cn": "等待解密[已锁]",
                "code_name": "",
                "is_hub": True,
                "is_proposal": False,
                "html_body": "<p>[SCP-001 枢纽页无有效正文内容]</p>",
                "images": []
            }

        # 清除“你已经被警告过了。”到下一张图片之间的全部空格/换行/空段落 (消除原网页制造滚动的240+个<br>导致的多页空白)
        warn_tag = content_div.find(lambda t: t.name in ["h1", "h2", "p", "span", "div"] and "你已经被警告过了" in t.get_text())
        if warn_tag:
            curr = warn_tag
            while curr and curr.parent != content_div:
                curr = curr.parent

            nodes_to_remove = []
            sib = curr.next_sibling if curr else None
            while sib:
                has_img = False
                if isinstance(sib, Tag):
                    if sib.name == "img" or sib.find("img"):
                        has_img = True
                if has_img:
                    break

                if isinstance(sib, Tag):
                    if not sib.get_text(strip=True):
                        if sib.name != "hr":
                            nodes_to_remove.append(sib)
                else:
                    if not str(sib).strip():
                        nodes_to_remove.append(sib)
                sib = sib.next_sibling

            for node in nodes_to_remove:
                if hasattr(node, "decompose"):
                    node.decompose()
                elif hasattr(node, "extract"):
                    node.extract()

        # 移除静态打印不需要的 JS 选项卡按钮与空 iframe，展示完整提案目录列表
        for nav in content_div.find_all(class_="yui-nav"):
            nav.decompose()
        tab0 = content_div.find(id="wiki-tab-0-0")
        if tab0:
            tab0.decompose()
        tab1 = content_div.find(id="wiki-tab-0-1")
        if tab1:
            if tab1.has_attr("style"):
                del tab1["style"]

        # 将提案列表中的链接重定向为页内锚点跳转，例如 href="#jonathan-ball-s-proposal"
        for a in content_div.find_all("a"):
            href = a.get("href", "")
            if href and not href.startswith("#"):
                clean_target = href.rsplit("/", 1)[-1]
                clean_target = clean_target.replace("old%3A", "old-").replace("%3A", "-")
                a["href"] = f"#{clean_target}"

        inner_html, images = self._clean_content_div(content_div, soup, slug, context="SCP-001 枢纽页")

        return {
            "num": 1,
            "slug": slug,
            "marker": "#SCPMARK001HUB#",
            "title_en": "SCP-001 Awaiting De-classification [Blocked]",
            "title_cn": "等待解密[已锁]",
            "code_name": "",
            "is_hub": True,
            "is_proposal": False,
            "html_body": inner_html,
            "images": images
        }

    def parse_scp001_proposal(self, meta: Dict[str, str], html_text: str) -> Dict[str, Any]:
        """
        解析单个 SCP-001 提案页面正文。
        :param meta: 提案元数据字典（包含 slug, code_name, title, display, page_url）
        :param html_text: 提案网页原始 HTML
        """
        slug = meta["slug"]
        code_name = meta.get("code_name", "SCP-001 提案")
        title = meta.get("title", "")
        title_cn = title if title else code_name
        proposal_context = f"001提案: {meta.get('display', slug)}"
        page_url = meta.get("page_url", "")

        soup = BeautifulSoup(html_text, "html.parser")

        # 针对《廷达洛斯三位一体》：包含 offset/1, offset/2, offset/3 三个独立界面的多节结构
        tindalos_sections = soup.find_all("div", class_="tindalos-section")
        if tindalos_sections:
            combined_html_parts = []
            all_images = []
            for sec in tindalos_sections:
                part_num = sec.get("data-part", "")
                part_title = sec.get("data-title", f"第 {part_num} 部分")

                # 移除原网页底部的跳转链接与返回上层按钮
                for cl in sec.find_all("div", class_="customlink"):
                    cl.decompose()
                for a in sec.find_all("a"):
                    if a.get("href") and ("offset" in a.get("href") or "返回上层" in a.get_text()):
                        parent_p = a.find_parent("p")
                        if parent_p:
                            parent_p.decompose()
                        else:
                            a.decompose()

                sec_content = sec.find("div", id="page-content") or sec
                sec_inner, sec_imgs = self._clean_content_div(
                    sec_content, soup, f"{slug}_p{part_num}",
                    context=f"{proposal_context} (分部{part_num})",
                    page_url=page_url
                )
                all_images.extend(sec_imgs)

                header_box = f'''<div class="tindalos-part-header" style="margin: 16pt 0 8pt 0; padding: 4pt 8pt; background: #f2f2f2; border-left: 4pt solid #333333;">
                    <strong style="font-size: 10pt; color: #111111;">【分部 {part_num} · {part_title}】</strong>
                </div>'''
                combined_html_parts.append(header_box + sec_inner)

            final_combined_body = '<div class="tindalos-article">' + '<hr style="border: none; border-top: 1px dashed #cccccc; margin: 20pt auto; width: 85%;">' .join(combined_html_parts) + '</div>'
            return {
                "num": 0,
                "slug": slug,
                "marker": f"#SCPMARK_PROP_{slug}#",
                "title_en": f"SCP-001 {code_name}",
                "title_cn": title_cn,
                "code_name": code_name,
                "is_hub": False,
                "is_proposal": True,
                "html_body": final_combined_body,
                "images": all_images
            }

        content_div = soup.find("div", id="page-content")

        if not content_div:
            return {
                "num": 0,
                "slug": slug,
                "marker": f"#SCPMARK_PROP_{slug}#",
                "title_en": f"SCP-001 {code_name}",
                "title_cn": title_cn,
                "code_name": code_name,
                "is_hub": False,
                "is_proposal": True,
                "html_body": "<p>[提案页面无有效正文内容]</p>",
                "images": []
            }

        inner_html, images = self._clean_content_div(content_div, soup, slug, context=proposal_context, page_url=page_url)

        return {
            "num": 0,
            "slug": slug,
            "marker": f"#SCPMARK_PROP_{slug}#",
            "title_en": f"SCP-001 {code_name}",
            "title_cn": title_cn,
            "code_name": code_name,
            "is_hub": False,
            "is_proposal": True,
            "html_body": inner_html,
            "images": images
        }
