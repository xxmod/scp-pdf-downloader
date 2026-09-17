# -*- coding: utf-8 -*-
"""
SCP 档案网络下载与缓存模块
负责从本地 Kiwix/SCP 镜像站抓取系列索引、条目 HTML 及相关插图资源。
具备断点缓存与自动容错跳过机制。
"""

import os
import sys
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple


class SCPCrawler:
    def __init__(self, base_url: str = "http://192.168.6.138:8080/viewer#scp-wiki-cn_2026-05/scp-wiki-cn.wikidot.com/scp-",
                 cache_dir: str = "data", skip_download: bool = False):
        """
        初始化 SCP 爬虫。
        :param base_url: 以 scp- 结尾的页面 URL 前缀（如 viewer# 形式或 content 形式）
        :param cache_dir: 本地缓存根目录
        :param skip_download: 是否启用完全离线模式（跳过一切未命中缓存的网络请求）
        """
        self.raw_base_url = base_url.strip()
        self.cache_dir = os.path.abspath(cache_dir)
        self.skip_download = skip_download
        self.html_dir = os.path.join(self.cache_dir, "html")
        self.image_dir = os.path.join(self.cache_dir, "images")
        
        os.makedirs(self.html_dir, exist_ok=True)
        os.makedirs(self.image_dir, exist_ok=True)

        # 将前端 viewer 形式的 URL 转换为直接获取静态 HTML 的 content 路径
        self.content_base_url, self.server_root = self._normalize_base_url(self.raw_base_url)
        self.titles_dict: Dict[int, str] = {}
        self.titles_cache_path = os.path.join(self.cache_dir, "titles_cache.json")
        
        # 失败/404 图片黑名单缓存，避免每次构建重复重试无用网络连接
        self.failed_images_cache_path = os.path.join(self.cache_dir, "failed_images_cache.json")
        self.failed_images = set()
        if os.path.exists(self.failed_images_cache_path):
            try:
                with open(self.failed_images_cache_path, "r", encoding="utf-8") as fp:
                    self.failed_images = set(json.load(fp))
            except Exception:
                self.failed_images = set()

    def _normalize_base_url(self, raw_url: str) -> Tuple[str, str]:
        """
        规范化基础 URL，兼容 viewer# 链接与直接 content 链接。
        """
        parsed = urllib.parse.urlparse(raw_url)
        server_root = f"{parsed.scheme}://{parsed.netloc}"

        if "/viewer#" in raw_url:
            # 例如: http://192.168.6.138:8080/viewer#scp-wiki-cn_2026-05/scp-wiki-cn.wikidot.com/scp-
            fragment = raw_url.split("/viewer#", 1)[1]
            content_url = f"{server_root}/content/{fragment}"
        else:
            content_url = raw_url

        if not content_url.endswith("scp-"):
            if not content_url.endswith("/"):
                content_url += "/"
            content_url += "scp-"

        return content_url, server_root

    def get_slug(self, scp_num: int) -> str:
        """
        获取标准 SCP 标识符，如 2 -> scp-002, 1000 -> scp-1000
        """
        if scp_num < 1000:
            return f"scp-{scp_num:03d}"
        return f"scp-{scp_num}"

    def fetch_series_titles(self, force: bool = False) -> Dict[int, str]:
        """
        从本地镜像抓取所有系列索引页（scp-series, scp-series-2 至 scp-series-9），
        解析并建立 {编号: 中文标题} 的映射字典。
        """
        if not force and os.path.exists(self.titles_cache_path):
            try:
                with open(self.titles_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.titles_dict = {int(k): v for k, v in data.items()}
                    print(f"[索引] 从本地缓存加载了 {len(self.titles_dict)} 条 SCP 中文标题")
                    return self.titles_dict
            except Exception as e:
                print(f"[警告] 读取标题缓存失败: {e}，将重新抓取索引")

        print("[索引] 正在从本地镜像抓取 SCP 官方系列索引页建立中文标题映射表...")
        # 推导系列页的基础 URL 路径
        # content_base_url 形如: http://.../content/scp-wiki-cn_2026-05/scp-wiki-cn.wikidot.com/scp-
        prefix = self.content_base_url[:-4] # 去除最后的 'scp-'
        
        series_slugs = ["scp-series"] + [f"scp-series-{i}" for i in range(2, 10)]
        
        for slug in series_slugs:
            series_url = f"{prefix}{slug}"
            try:
                req = urllib.request.Request(series_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                
                soup = BeautifulSoup(html, "html.parser")
                content = soup.find("div", id="page-content")
                if not content:
                    continue

                for li in content.find_all("li"):
                    text = li.text.strip()
                    # 匹配格式: SCP-002 - “生活”室 或 SCP-002 - “生活”室 (Heritage)
                    match = re.search(r"SCP-(\d+)\s*[-–—]\s*(.+)", text)
                    if match:
                        num = int(match.group(1))
                        title = match.group(2).strip()
                        # 清理可能带有的标签注释
                        title = re.sub(r"\s*\(.*?\)$", "", title).strip()
                        self.titles_dict[num] = title

                print(f"  - 成功解析索引页 {slug}，当前已收集 {len(self.titles_dict)} 个条目标题")
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    # 部分未开辟的 series 索引可能为 404，正常忽略
                    continue
                print(f"  - 索引页 {slug} 获取失败: {he}")
            except Exception as e:
                print(f"  - 索引页 {slug} 获取异常: {e}")

        # 写入缓存
        with open(self.titles_cache_path, "w", encoding="utf-8") as f:
            json.dump(self.titles_dict, f, ensure_ascii=False, indent=2)

        return self.titles_dict

    def download_page(self, scp_num: int, force: bool = False) -> Optional[str]:
        """
        下载单个 SCP 条目的原始 HTML 页面。
        遇到 404 或访问失败时自动跳过并返回 None。
        """
        slug = self.get_slug(scp_num)
        html_file = os.path.join(self.html_dir, f"{slug}.html")

        if not force and os.path.exists(html_file):
            with open(html_file, "r", encoding="utf-8") as f:
                return f.read()

        page_url = f"{self.content_base_url}{scp_num:03d}" if scp_num < 1000 else f"{self.content_base_url}{scp_num}"

        try:
            req = urllib.request.Request(page_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    print(f"[{slug}] 状态码非 200 ({resp.status})，自动跳过")
                    return None
                html_bytes = resp.read()
                html_text = html_bytes.decode("utf-8", errors="ignore")

            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_text)
            return html_text

        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"[{slug}] 页面不存在 (HTTP 404)，自动跳过")
            else:
                print(f"[{slug}] HTTP 错误: {e.code}，自动跳过")
            return None
        except Exception as e:
            print(f"[{slug}] 请求异常 ({e})，自动跳过")
            return None

    def download_image(self, img_url: str, save_name: str, context: str = "", page_url: Optional[str] = None) -> Optional[str]:
        """
        下载并保存图片到本地 images 缓存目录。
        :param img_url: 页面中的图片相对或绝对路径
        :param save_name: 保存的文件名
        :param context: 调用上下文描述（如文章或提案标题），用于定位日志
        :param page_url: 当前网页的绝对 URL，用于精准计算多级相对路径
        :return: 本地文件的相对或绝对路径，下载失败返回 None
        """
        save_path = os.path.join(self.image_dir, save_name)
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            return save_path

        # 离线模式：如果启用了 --skip-download 且本地无图，直接返回 None，杜绝阻塞网络请求
        if self.skip_download:
            return None

        # 黑名单拦截：若该图片此前已确认 404 或失效，直接返回 None，耗时 0 毫秒
        if img_url in self.failed_images:
            return None

        # 拼接绝对请求 URL
        if img_url.startswith("http://") or img_url.startswith("https://"):
            full_url = img_url
        elif page_url:
            full_url = urllib.parse.urljoin(page_url, img_url)
        elif img_url.startswith("/"):
            full_url = f"{self.server_root}{img_url}"
        else:
            full_url = urllib.parse.urljoin(self.content_base_url, img_url)

        try:
            req = urllib.request.Request(full_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    with open(save_path, "wb") as f:
                        f.write(resp.read())
                    # 用户要求：将图片压缩，图片质量在60即可
                    self._compress_image(save_path, quality=60)
                    return save_path
        except Exception as e:
            # 记录失败图片到黑名单并持久化
            self.failed_images.add(img_url)
            try:
                with open(self.failed_images_cache_path, "w", encoding="utf-8") as fp:
                    json.dump(list(self.failed_images), fp, ensure_ascii=False)
            except Exception:
                pass

            prefix = f"[{context}] " if context else ""
            print(f"  [图片下载警告] {prefix}无法下载图片 {img_url}: {e} (已加入黑名单跳过后续重试)", flush=True)
            return None

    def _compress_image(self, image_path: str, quality: int = 60):
        """将图片压缩为 quality=60 的 JPEG 格式以减小体积"""
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[-1])
                    target = bg
                else:
                    target = img.convert('RGB')
                temp_path = image_path + ".tmp.jpg"
                target.save(temp_path, 'JPEG', quality=quality, optimize=True)
            os.replace(temp_path, image_path)
        except Exception as e:
            pass

    def download_range(self, start_id: int, end_id: int, force: bool = False) -> List[Tuple[int, str]]:
        """
        批量下载指定编号范围内的所有 SCP 页面及图片。
        :return: 成功下载的 [(编号, HTML内容)] 列表
        """
        self.fetch_series_titles()
        results = []
        total = end_id - start_id + 1
        print(f"\n[开始抓取] 目标区间: SCP-{start_id:03d} 到 SCP-{end_id:03d} (共计 {total} 篇)")

        success_count = 0
        skip_count = 0

        for idx, scp_num in enumerate(range(start_id, end_id + 1), start=1):
            if scp_num == 1:
                # SCP-001 归入 001 提案集模块（等待解密[已锁]），跳过常规条目抓取，杜绝生成内容重复的“首中之重”
                continue
            slug = self.get_slug(scp_num)
            title = self.titles_dict.get(scp_num, "")
            title_display = f" - {title}" if title else ""

            print(f"[{idx}/{total}] 正在获取 {slug.upper()}{title_display}...", end="\r", flush=True)
            html = self.download_page(scp_num, force=force)
            if html:
                results.append((scp_num, html))
                success_count += 1
            else:
                skip_count += 1

        print(f"\n[抓取完成] 成功下载/已缓存: {success_count} 篇，跳过/不存在: {skip_count} 篇")
        return results

    EXCLUDED_PROPOSALS = {
        "ouroboros",                   # 衔尾蛇
        "old-kalinins-proposal",       # 过去与未来
        "not-a-seagull-proposal",      # 港口上的天空
        "001-blank-i",                 # 黑暗再临
        "rounderhouse-bone-proposal",  # 黑色内殿
        "plague-s-proposal",           # 脱逃者
    }
    EXCLUDED_TITLES = {"衔尾蛇", "过去与未来", "港口上的天空", "黑暗再临", "黑色内殿", "脱逃者"}

    def fetch_scp001_proposals_meta(self, force: bool = False) -> List[Dict[str, str]]:
        """
        从本地镜像的 scp-001 枢纽页面解析所有提案的元数据列表。
        自动过滤用户指定的排除提案，并对交互式提案进行特殊定向。
        """
        cache_file = os.path.join(self.cache_dir, "proposals_cache.json")
        if not force and os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    # 二次过滤，确保缓存中若残留排除提案也能被剔除
                    filtered_cached = [
                        p for p in cached
                        if p.get("slug") not in self.EXCLUDED_PROPOSALS
                        and not any(ex in p.get("slug", "") for ex in self.EXCLUDED_PROPOSALS)
                        and not any(t in p.get("title", "") for t in self.EXCLUDED_TITLES)
                    ]
                    return filtered_cached
            except Exception:
                pass

        hub_url = self.content_base_url.rsplit("scp-", 1)[0] + "scp-001"
        try:
            req = urllib.request.Request(hub_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[SCP-001抓取错误] 无法获取 001 枢纽页: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        pc = soup.find("div", id="page-content")
        if not pc:
            return []

        proposals = []
        for p in pc.find_all("p"):
            a = p.find("a")
            if a and a.get("href"):
                href = a["href"].strip()
                code_name = a.get_text().strip()
                full_text = p.get_text().strip()
                if "代号：" in code_name or (" - " in full_text and "proposal" in href):
                    parts = full_text.split(" - ", 1)
                    title = parts[1].strip() if len(parts) > 1 else ""
                    clean_slug = href
                    if "://" in clean_slug:
                        clean_slug = clean_slug.split("/")[-1]
                    clean_slug = clean_slug.replace("old%3A", "old-").replace("%3A", "-")

                    # 1. 过滤排除提案（衔尾蛇、过去与未来、港口上的天空、黑暗再临、黑色内殿、脱逃者）
                    if clean_slug in self.EXCLUDED_PROPOSALS or any(ex in clean_slug for ex in self.EXCLUDED_PROPOSALS):
                        continue
                    if any(t in title for t in self.EXCLUDED_TITLES):
                        continue

                    # 2. 提案《门面》需要互动进入 offset/1 页面
                    if clean_slug == "pickman-blank-proposal":
                        href = "pickman-blank-proposal/offset/1"

                    proposals.append({
                        "raw_href": href,
                        "slug": clean_slug,
                        "code_name": code_name,
                        "title": title,
                        "display": f"{code_name} - {title}" if title else code_name
                    })

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(proposals, f, ensure_ascii=False, indent=2)

        return proposals

    def download_scp001_hub_and_proposals(self, force: bool = False) -> Tuple[Optional[str], List[Tuple[Dict[str, str], str]]]:
        """
        下载 SCP-001 枢纽页面及全部提案 HTML。
        :return: (hub_html, [(proposal_meta, html_text), ...])
        """
        proposals_meta = self.fetch_scp001_proposals_meta(force=force)
        print(f"\n[SCP-001抓取] 正在处理 SCP-001 枢纽页与 {len(proposals_meta)} 个提案...")

        # 1. 枢纽页
        hub_save_path = os.path.join(self.html_dir, "scp-001.html")
        hub_html = None
        if not force and os.path.exists(hub_save_path):
            with open(hub_save_path, "r", encoding="utf-8", errors="ignore") as f:
                hub_html = f.read()
        else:
            hub_url = self.content_base_url.rsplit("scp-", 1)[0] + "scp-001"
            try:
                req = urllib.request.Request(hub_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw_data = resp.read()
                    with open(hub_save_path, "wb") as fp:
                        fp.write(raw_data)
                    hub_html = raw_data.decode("utf-8", errors="ignore")
            except Exception as e:
                print(f"  [SCP-001枢纽页下载错误]: {e}")

        # 2. 提案页
        proposals_dir = os.path.join(self.html_dir, "proposals")
        os.makedirs(proposals_dir, exist_ok=True)
        base_prefix = self.content_base_url.rsplit("scp-", 1)[0]

        results = []
        for idx, p in enumerate(proposals_meta, start=1):
            slug = p["slug"]
            raw = p["raw_href"]
            save_file = os.path.join(proposals_dir, f"{slug}.html")

            disp = p.get('display', '')[:30].encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace')
            print(f"[{idx}/{len(proposals_meta)}] 获取 001提案: {disp}...", end="\r", flush=True)
            target_url = f"{base_prefix}{raw}" if not raw.startswith("http") else f"{base_prefix}{raw.split('/')[-1]}"
            p["page_url"] = target_url

            # 针对《廷达洛斯三位一体》：需要进入 offset/1, offset/2, offset/3 三个界面并整合
            if slug == "jack-ike-s-proposal-ii":
                if not force and os.path.exists(save_file):
                    with open(save_file, "r", encoding="utf-8", errors="ignore") as fp:
                        html_text = fp.read()
                    if "tindalos-section" in html_text:
                        results.append((p, html_text))
                        continue

                offsets = [
                    (1, "凯撒憎恶 · HATED CAESAR", "https://scp-wiki-cn.wikidot.com/jack-ike-s-proposal-ii/offset/1"),
                    (2, "领主谴责 · OVERLORD CENSURE", "https://scp-wiki-cn.wikidot.com/jack-ike-s-proposal-ii/offset/2"),
                    (3, "压抑拒绝 · OPPRESS WITHHOLD", "https://scp-wiki-cn.wikidot.com/jack-ike-s-proposal-ii/offset/3")
                ]
                sections_html = []
                for o_num, o_title, o_public_url in offsets:
                    local_url = f"{base_prefix}jack-ike-s-proposal-ii/offset/{o_num}"
                    o_html = None
                    try:
                        req = urllib.request.Request(local_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
                        with urllib.request.urlopen(req, timeout=5) as r:
                            if r.status == 200:
                                o_html = r.read().decode("utf-8", errors="ignore")
                    except Exception:
                        pass

                    if not o_html:
                        try:
                            req = urllib.request.Request(o_public_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                            with urllib.request.urlopen(req, timeout=12) as r:
                                o_html = r.read().decode("utf-8", errors="ignore")
                        except Exception as e:
                            print(f"\n  [廷达洛斯抓取警告] 无法获取 offset/{o_num}: {e}")

                    if o_html:
                        o_soup = BeautifulSoup(o_html, "html.parser")
                        o_pc = o_soup.find("div", id="page-content")
                        inner = str(o_pc) if o_pc else o_html
                        sections_html.append(f'<div class="tindalos-section" data-part="{o_num}" data-title="{o_title}">\n{inner}\n</div>')

                combined_html = f'<div id="page-content" class="tindalos-container">\n' + "\n".join(sections_html) + "\n</div>"
                with open(save_file, "w", encoding="utf-8") as fp:
                    fp.write(combined_html)
                results.append((p, combined_html))
                continue

            # 普通提案缓存检查
            if not force and os.path.exists(save_file):
                with open(save_file, "r", encoding="utf-8", errors="ignore") as fp:
                    html_text = fp.read()
                # 若为旧版未互动的门面页面（未包含真实正文 PoI-001 档案），重新下载 offset/1
                if slug == "pickman-blank-proposal" and "PoI-001" not in html_text:
                    pass
                else:
                    results.append((p, html_text))
                    continue

            try:
                req = urllib.request.Request(target_url, headers={"User-Agent": "SCPPdfBuilder/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    raw_bytes = r.read()
                    with open(save_file, "wb") as fp:
                        fp.write(raw_bytes)
                    html_text = raw_bytes.decode("utf-8", errors="ignore")
                results.append((p, html_text))
            except Exception:
                pass

        print(f"\n[SCP-001抓取完成] 成功获取 {len(results)} 个提案文档。")
        return hub_html, results
