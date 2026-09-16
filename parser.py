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
            "#page-version-info"
        ]
        for sel in unwanted_selectors:
            for tag in content_div.select(sel):
                tag.decompose()

        # 2. 提取与处理图片块 (div.scp-image-block)
        images = []
        img_blocks = content_div.find_all("div", class_="scp-image-block")
        for idx, block in enumerate(img_blocks, start=1):
            img_tag = block.find("img")
            if not img_tag or not img_tag.get("src"):
                block.decompose()
                continue

            src = img_tag["src"]
            # 提取 caption
            caption_tag = block.find("div", class_="scp-image-caption")
            caption_text = caption_tag.get_text().strip() if caption_tag else ""

            # 保存图片到本地
            img_ext = os.path.splitext(src.split("?")[0])[1] or ".jpg"
            save_name = f"{slug.upper()}_{idx}{img_ext}" if idx > 1 else f"{slug.upper()}{img_ext}"
            local_img_path = self.crawler.download_image(src, save_name)

            if local_img_path and os.path.exists(local_img_path):
                # 转为供 HTML 模板使用的绝对 file:/// URI 路径，彻底杜绝相对路径偏差
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

        # 3. 处理折叠块 (collapsible-block)：在打印版中直接展开其内容
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

        # 4. 标准化 blockquote 为 scpbox
        for bq in content_div.find_all("blockquote"):
            bq["class"] = bq.get("class", []) + ["scpbox"]

        # 5. 关键标签加粗规范化（项目编号、项目等级、特殊收容措施、描述）
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
