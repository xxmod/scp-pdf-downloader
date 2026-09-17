# -*- coding: utf-8 -*-
"""
SCP 档案下载与高保真 PDF 整合工具主程序
默认目标: 下载并整合 SCP-001（含枢纽页与全部多提案）及 SCP-002 至 SCP-200（可扩展至 SCP-9999）
输出形似 scp.op3.notofira.v1.19.pdf 的移动版高保真 PDF 电子书，
并兼容补齐 data/scp-pdf-master 的 LaTeX 源码。
"""

import os
import sys
import argparse
import time
import shutil
from typing import List, Dict, Any

from crawler import SCPCrawler
from parser import SCPParser
from pdf_builder import SCPPdfBuilder
from tex_exporter import SCPTexExporter


def parse_args():
    parser = argparse.ArgumentParser(
        description="SCP 档案下载与高保真 PDF 整合工具 (支持 SCP-001多提案 及 SCP-002至SCP-9999)"
    )
    parser.add_argument(
        "--start",
        type=int,
        default=2,
        help="起始常规 SCP 编号 (默认: 2)"
    )
    parser.add_argument(
        "--end",
        type=int,
        default=200,
        help="结束常规 SCP 编号 (默认: 200)"
    )
    parser.add_argument(
        "--include-001",
        action="store_true",
        default=True,
        help="收录 SCP-001 枢纽页及全部多提案 (默认开启)"
    )
    parser.add_argument(
        "--no-001",
        dest="include_001",
        action="store_false",
        help="禁用 SCP-001 提案收录"
    )
    parser.add_argument(
        "--host",
        "--base-url",
        dest="base_url",
        type=str,
        default="http://192.168.6.138:8080/viewer#scp-wiki-cn_2026-05/scp-wiki-cn.wikidot.com/scp-",
        help="SCP 镜像站点 base URL (以 scp- 结尾)"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="scp.op3.v1.20_scp001-200.pdf",
        help="输出 PDF 文件名或路径 (默认: scp.op3.v1.20_scp001-200.pdf)"
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="跳过网络抓取步骤，直接利用已有的本地缓存构建 PDF"
    )
    parser.add_argument(
        "--export-tex",
        action="store_true",
        default=True,
        help="同步补齐 data/scp-pdf-master 中的 LaTeX 源码 (默认开启)"
    )
    return parser.parse_args()


def main():
    # 确保控制台中文输出正常
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    start_time = time.time()

    print("=" * 60)
    print("      SCP 基金会档案下载与整合 PDF 构建系统")
    print(f"  收录 001 提案: {'是' if args.include_001 else '否'}")
    print(f"  常规条目范围: SCP-{args.start:03d} ~ SCP-{args.end:03d}")
    print(f"  镜像地址: {args.base_url}")
    print(f"  输出文件: {args.output}")
    print("=" * 60)

    # 1. 初始化爬虫并建立官方标题索引
    crawler = SCPCrawler(base_url=args.base_url)
    titles_dict = crawler.fetch_series_titles()

    parsed_items: List[Dict[str, Any]] = []
    parser = SCPParser(crawler)

    # 2. 处理 SCP-001 枢纽页与全部提案
    if args.include_001:
        print("\n[SCP-001模块] 正在获取并解析 SCP-001 枢纽页与全部提案...", flush=True)
        hub_html, prop_htmls = crawler.download_scp001_hub_and_proposals(force=False if args.skip_download else False)
        
        # 2.1 解析枢纽页
        if hub_html:
            try:
                hub_item = parser.parse_scp001_hub(hub_html)
                parsed_items.append(hub_item)
                print("  - 已成功解析并结构化 SCP-001 枢纽页 (含最高机密告示与模因抹杀触媒大图)", flush=True)
            except Exception as e:
                print(f"  [警告] 解析 SCP-001 枢纽页异常: {e}", flush=True)

        # 2.2 解析各提案
        total_props = len(prop_htmls)
        print(f"  - 开始逐篇解析清洗 {total_props} 篇 SCP-001 提案正文...", flush=True)
        for idx, (meta, p_html) in enumerate(prop_htmls, start=1):
            try:
                prop_item = parser.parse_scp001_proposal(meta, p_html)
                parsed_items.append(prop_item)
                if idx % 10 == 0 or idx == total_props or idx <= 3:
                    pct = idx / total_props * 100
                    print(f"    * [001提案解析] 进度: {idx}/{total_props} ({pct:.1f}%) | 当前: {meta.get('display', '')[:30]}", flush=True)
            except Exception as e:
                print(f"  [警告] 解析提案 {meta.get('display')} 异常: {e}", flush=True)

        print(f"[SCP-001模块完成] 成功收录 {len(parsed_items)} 篇 001 相关文档\n", flush=True)

    # 3. 抓取与读取常规条目 HTML (SCP-002 ~ SCP-200)
    html_items = []
    if not args.skip_download:
        html_items = crawler.download_range(start_id=args.start, end_id=args.end)
    else:
        print("[常规条目] 已启用 --skip-download，正在从本地磁盘缓存读取常规条目 HTML...", flush=True)
        for scp_num in range(args.start, args.end + 1):
            slug = crawler.get_slug(scp_num)
            html_file = os.path.join(crawler.html_dir, f"{slug}.html")
            if os.path.exists(html_file):
                with open(html_file, "r", encoding="utf-8", errors="ignore") as f:
                    html_items.append((scp_num, f.read()))
            else:
                print(f"  [提示] 本地缓存中无 {slug}.html，自动跳过", flush=True)

    # 4. 解析清洗常规条目
    total_regs = len(html_items)
    print(f"\n[内容解析] 正在解析清洗 {total_regs} 篇常规 SCP 档案内容...", flush=True)
    for idx, (scp_num, html_text) in enumerate(html_items, start=1):
        title_cn = titles_dict.get(scp_num, "")
        try:
            item_data = parser.parse(scp_num, html_text, custom_title=title_cn)
            parsed_items.append(item_data)
            if idx % 25 == 0 or idx == total_regs or idx <= 3:
                pct = idx / total_regs * 100
                print(f"    * [常规条目解析] 进度: {idx}/{total_regs} ({pct:.1f}%) | 当前: SCP-{scp_num:03d} {title_cn}", flush=True)
        except Exception as e:
            print(f"  [警告] 解析 SCP-{scp_num:03d} 发生异常: {e}，跳过此篇", flush=True)

    print(f"[内容解析完成] 全书共计就绪 {len(parsed_items)} 篇结构化文档\n", flush=True)

    # 5. 可选: 导出 LaTeX 源码并补齐 data/scp-pdf-master (重点补充 191~200 等缺失篇目)
    if args.export_tex:
        exporter = SCPTexExporter()
        if os.path.exists(exporter.master_dir):
            print(f"\n[LaTeX导出] 检查并补全 {exporter.master_dir} 源码...")
            for item in parsed_items:
                num = item.get("num", 0)
                # 如果是 101~200 区间且非 001 提案，输出到 part02
                if 101 <= num <= 200 and not item.get("is_proposal") and not item.get("is_hub"):
                    tex_file = os.path.join(exporter.part02_dir, f"{num}.tex")
                    if not os.path.exists(tex_file):
                        tex_code = exporter.export_item(item)
                        with open(tex_file, "w", encoding="utf-8") as f:
                            f.write(tex_code)
                        print(f"  - 已补全 LaTeX 篇目: part02/{num}.tex")
                    # 同步图片到 master/images
                    for img_info in item.get("images", []):
                        src_img = img_info["path"]
                        dst_img = os.path.join(exporter.images_dir, os.path.basename(src_img))
                        if os.path.exists(src_img) and not os.path.exists(dst_img):
                            shutil.copy2(src_img, dst_img)

            # 更新 part02 索引
            exporter.update_part02_index(start_num=191, end_num=200)
        else:
            print(f"\n[LaTeX导出] 未检测到 {exporter.master_dir} 源码目录，跳过 LaTeX 补全。")

    # 6. 高保真 PDF 构建
    print("\n[PDF生成] 启动 Playwright 引擎渲染并合成 PDF...")
    builder = SCPPdfBuilder()
    output_pdf = builder.build_pdf(
        items=parsed_items,
        output_pdf_path=args.output,
        version="1.20"
    )

    elapsed = time.time() - start_time
    file_size_mb = os.path.getsize(output_pdf) / (1024 * 1024)

    print("\n" + "=" * 60)
    print("               任务全部完成！")
    print(f"  已整合篇目: {len(parsed_items)} 篇 (含 001 枢纽及提案 + SCP-{args.start:03d} ~ SCP-{args.end:03d})")
    print(f"  最终 PDF 路径: {output_pdf}")
    print(f"  文件大小: {file_size_mb:.2f} MB")
    print(f"  总耗时: {elapsed:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
