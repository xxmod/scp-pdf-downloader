# -*- coding: utf-8 -*-
"""
SCP 页面内容清洗与结构化解析模块
负责将抓取的原始 HTML 页面解析为规范的条目数据（标题、等级、收容措施、描述、插图、引用框等）
"""

import os
import re
from bs4 import BeautifulSoup, Tag, NavigableString
from typing import Dict, Any, List, Optional
from crawler import SCPCrawler


class SCPParser:
    def __init__(self, crawler: Optional[SCPCrawler] = None):
        """
        初始化 SCP 解析器。
        :param crawler: SCPCrawler 实例（用于辅助下载页面中引用的图片）
        """
        self.crawler = crawler or SCPCrawler()

    def parse(self, scp_num: int, html_text: str, custom_title: str = "") -> Dict[str, Any]:
        """
        解析单个 SCP 页面的 HTML。
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
                "title_en": f"{slug.upper()}",
                "title_cn": custom_title or f"{slug.upper()}",
                "html_body": f"<p>[页面无有效正文内容]</p>",
                "images": []
            }

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
            # 新增：作者推广条与作品信息栏
            ".authorlink-wrapper",
            ".authorbox",
            ".authorcontent",
            ".info-container",
            ".heritage-rating-module",
            ".modal-wrapper",
            ".modal",
            "#toc"
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

            img_ext = os.path.splitext(src.split("?")[0])[1] or ".jpg"
            save_name = f"{slug.upper()}_{idx}{img_ext}" if idx > 1 else f"{slug.upper()}{img_ext}"
            local_img_path = self.crawler.download_image(src, save_name)

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
            # 若已是规范化的本地绝对路径，则跳过
            src = extra_img.get("src", "")
            if not src or src.startswith("file:///"):
                continue

            # 过滤掉无关用户头像或微小图标
            if any(k in src for k in ["avatar.php", "userkarma.php", "favicon", "local--favicon", "default.png"]):
                extra_img.decompose()
                continue

            # 下载非标准插图（支持 wdfiles、表格插图、警告框Logo等）
            raw_fname = os.path.basename(src.split("?")[0])
            if not raw_fname or len(raw_fname) > 50:
                raw_fname = f"extra_{extra_idx}.jpg"
            save_fname = f"{slug.upper()}_{raw_fname}"

            local_img_path = self.crawler.download_image(src, save_fname)
            if local_img_path and os.path.exists(local_img_path):
                abs_img_uri = f"file:///{local_img_path.replace(os.sep, '/')}"
                extra_img["src"] = abs_img_uri
                extra_img["style"] = (extra_img.get("style", "") + "; max-width: 100%; height: auto;").strip("; ")
                images.append({"path": local_img_path, "caption": ""})
            else:
                # 严密防线：如果下载失败或本地不存在，直接销毁该 img 标签，决不在 PDF 中留下破图图标！
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

        # 4. 处理折叠块 (collapsible-block)：在打印版中直接展开其内容
        for fold in content_div.find_all("div", class_="collapsible-block"):
            unfolded = fold.find("div", class_="collapsible-block-content")
            if unfolded:
                box_div = soup.new_tag("div", **{"class": "scpbox"})
                # 提取折叠标题
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

        # 5. 标准化 blockquote 为 scpbox
        for bq in content_div.find_all("blockquote"):
            bq["class"] = bq.get("class", []) + ["scpbox"]

        # 6. 清理可能写死大宽度的内联样式与浮动，杜绝横向溢出与内容重叠
        for tag in content_div.find_all(lambda t: t.has_attr("style")):
            st = tag["style"]
            if re.search(r"width:\s*\d{3,}px", st):
                tag["style"] = re.sub(r"width:\s*\d{3,}px", "max-width: 100%", st)
            if "margin" in tag["style"] and re.search(r"margin:\s*[^;]*\d{2,}px", tag["style"]):
                tag["style"] = re.sub(r"margin:\s*[^;]+;", "margin: 8pt auto;", tag["style"])
            if "float:" in tag["style"]:
                tag["style"] = re.sub(r"float:\s*[^;]+;", "float: none; margin: 6pt auto;", tag["style"])

        # 7. 关键标签加粗规范化（项目编号、项目等级、特殊收容措施、描述）
        # 先去除可能已存在的标签外层 strong，再统一处理
        body_html = str(content_div)
        labels = ["项目编号", "项目等级", "特殊收容措施", "描述", "附录"]
        for lbl in labels:
            # 清理 <strong>项目编号：</strong> 变成纯文本再统一加粗
            body_html = re.sub(rf"<strong>\s*({lbl}\s*[:：])\s*</strong>", r"\1", body_html)
            body_html = re.sub(rf"<b>\s*({lbl}\s*[:：])\s*</b>", r"\1", body_html)
            body_html = re.sub(rf"(?<!<strong>)({lbl}\s*[:：])", r"<strong>\1</strong>", body_html)

        # 重新包装
        body_soup = BeautifulSoup(body_html, "html.parser")
        final_div = body_soup.find("div", id="page-content")
        inner_html = "".join(str(c) for c in final_div.children) if final_div else body_html

        # 提取英文和中文标题
        title_cn = custom_title or f"{slug.upper()}"
        # 推测标准英文命名（若 series 字典中没有单独英文名，使用标准 SCP-xxx 标识）
        title_en = f"{slug.upper()}"

        return {
            "num": scp_num,
            "slug": slug,
            "title_en": title_en,
            "title_cn": title_cn,
            "html_body": inner_html,
            "images": images
        }
