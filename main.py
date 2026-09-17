# -*- coding: utf-8 -*-
"""
SCP 档案下载与高保真 PDF 整合工具主程序
默认目标: 下载并整合 SCP-001（含枢纽页与全部多提案）及 SCP-002 至 SCP-200（可扩展至 SCP-9999）
输出形似 scp.op3.notofira.v1.19.pdf 的移动版高保真 PDF 电子书。
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
        default=1,
        help="起始 SCP 编号 (默认: 1)"
    )
    parser.add_argument(
        "--end",
        type=int,
        default=200,
        help="结束常规 SCP 编号 (默认: 200)"
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
        help="输出 PDF 文件名或路径 (默认: scp.op3.v1.20_scp001-200.pdf，若指定 --split 则本参数无效)"
    )
    parser.add_argument(
        "--split",
        nargs="?",
        const=200,
        default=None,
        type=int,
        metavar="N",
        help="启用分段模式：每隔 N 个 SCP 分段生成独立的 PDF 文件 (不传 N 时默认 200，\n此时 --output 参数无效，文件命名为 scp_xxx-xxx.pdf)"
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="跳过网络抓取步骤，直接利用已有的本地缓存构建 PDF"
    )
    parser.add_argument(
        "--export-tex",
        action="store_true",
        default=False,
        help="将所有条目导出为 .tex 文件到项目根目录的 tex/ 文件夹 (默认关闭)"
    )
    return parser.parse_args()


def main():
    # 确保控制台中文输出正常
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    start_time = time.time()

    # SCP-001 枢纽页与提案仅在 --start 为 1 时处理
    include_001 = (args.start == 1)

    print("=" * 60)
    print("      SCP 基金会档案下载与整合 PDF 构建系统")
    if args.split is not None:
        print(f"  分段模式 (--split): 开启 (每 {args.split} 篇一个独立 PDF，忽略 --output)")
    print(f"  收录 001 提案: {'是' if include_001 else '否'}")
    print(f"  常规条目范围: SCP-{args.start:03d} ~ SCP-{args.end:03d}")
    print(f"  镜像地址: {args.base_url}")
    if args.split is None:
        print(f"  输出文件: {args.output}")
    print("=" * 60)

    # 1. 初始化爬虫并建立官方标题索引
    crawler = SCPCrawler(base_url=args.base_url, skip_download=args.skip_download)
    titles_dict = crawler.fetch_series_titles()

    parsed_items: List[Dict[str, Any]] = []
    parser = SCPParser(crawler)

    # 2. 处理 SCP-001 枢纽页与全部提案（仅 --start 1 时）
    if include_001:
        print("\n[SCP-001模块] 正在获取并解析 SCP-001 枢纽页与全部提案...", flush=True)
        hub_html, prop_htmls = crawler.download_scp001_hub_and_proposals(force=False if args.skip_download else False)
        
        # 2.1 解析枢纽页
        if hub_html:
            try:
                hub_item = parser.parse_scp001_hub(hub_html)
                parsed_items.append(hub_item)
                print("  - 已成功解析并结构化 SCP-001 枢纽页", flush=True)
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
    # 常规条目起始编号：若包含 001 模块，则常规条目从 2 开始，避免生成与“等待解密[已锁]”重复的“首中之重”
    regular_start = max(args.start, 2) if include_001 else args.start
    html_items = []
    if not args.skip_download:
        html_items = crawler.download_range(start_id=regular_start, end_id=args.end)
    else:
        print("[常规条目] 已启用 --skip-download，正在从本地磁盘缓存读取常规条目 HTML...", flush=True)
        for scp_num in range(regular_start, args.end + 1):
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
        if scp_num == 1:
            continue
        title_cn = titles_dict.get(scp_num, "")
        if title_cn == "首中之重":
            continue
        try:
            item_data = parser.parse(scp_num, html_text, custom_title=title_cn)
            parsed_items.append(item_data)
            if idx % 25 == 0 or idx == total_regs or idx <= 3:
                pct = idx / total_regs * 100
                print(f"    * [常规条目解析] 进度: {idx}/{total_regs} ({pct:.1f}%) | 当前: SCP-{scp_num:03d} {title_cn}", flush=True)
        except Exception as e:
            print(f"  [警告] 解析 SCP-{scp_num:03d} 发生异常: {e}，跳过此篇", flush=True)

    # 彻底过滤掉任何非枢纽页/非提案的常规 SCP-001 或标题为“首中之重”的重复条目
    parsed_items = [
        it for it in parsed_items
        if not (it.get("num") == 1 and not it.get("is_hub") and not it.get("is_proposal"))
        and it.get("title_cn") != "首中之重"
    ]

    print(f"[内容解析完成] 全书共计就绪 {len(parsed_items)} 篇结构化文档\n", flush=True)

    if args.export_tex:
        exporter = SCPTexExporter()  # 默认导出到 ./tex/
        print(f"\n[LaTeX导出] 正在将条目导出到 {exporter.tex_dir} ...")
        exported_count = 0
        for item in parsed_items:
            try:
                filepath = exporter.export_item_to_file(item)
                exported_count += 1
            except Exception as e:
                print(f"  [警告] 导出 {item.get('slug', '?')} 失败: {e}")
        exporter.update_part02_index()
        print(f"[LaTeX导出完成] 已导出 {exported_count} 篇至 {exporter.tex_dir}")

    # 6. 高保真 PDF 构建
    print("\n[PDF生成] 启动 Playwright 引擎渲染并合成 PDF...")
    builder = SCPPdfBuilder()

    if args.split is not None:
        chunk_size = args.split
        # 分段模式：每隔 chunk_size 个 SCP 分段
        chunks = []
        curr = args.start
        while curr <= args.end:
            chunk_end = min(curr + chunk_size - 1, args.end)
            chunks.append((curr, chunk_end))
            curr = chunk_end + 1

        print(f"\n[分段模式] 启用 --split {chunk_size} 参数，每 {chunk_size} 篇生成独立 PDF (--output 参数已自动忽略):")
        for idx, (cs, ce) in enumerate(chunks, start=1):
            c_out = f"scp_{cs:03d}-{ce:03d}.pdf"
            print(f"  - 分段 {idx}/{len(chunks)}: SCP-{cs:03d} ~ SCP-{ce:03d} -> {c_out}")

        generated_files = []

        for idx, (cs, ce) in enumerate(chunks, start=1):
            c_out = f"scp_{cs:03d}-{ce:03d}.pdf"
            print(f"\n{'=' * 25} 正在构建分段 {idx}/{len(chunks)}: {c_out} {'=' * 25}", flush=True)

            chunk_items = []
            # 若该分段覆盖 1 且包含 001 模块，放入 001 枢纽页与全部提案
            if cs <= 1 <= ce and include_001:
                for it in parsed_items:
                    if it.get("is_hub") or it.get("is_proposal"):
                        chunk_items.append(it)

            # 放入当前分段范围内的常规条目
            for it in parsed_items:
                if not it.get("is_hub") and not it.get("is_proposal"):
                    num = it.get("num", 0)
                    if cs <= num <= ce:
                        chunk_items.append(it)

            if not chunk_items:
                print(f"  [提示] 分段 {c_out} 内无有效条目，跳过生成", flush=True)
                continue

            print(f"  - 本分段共收录 {len(chunk_items)} 篇文档，包含专属封面与目录，开始渲染排版...", flush=True)
            chunk_pdf = builder.build_pdf(
                items=chunk_items,
                output_pdf_path=c_out,
                version="1.20"
            )
            chunk_mb = os.path.getsize(chunk_pdf) / (1024 * 1024)
            generated_files.append((c_out, chunk_pdf, len(chunk_items), chunk_mb))
            print(f">>> 分段 {idx}/{len(chunks)}: {c_out} 构建成功！(共 {len(chunk_items)} 篇文档, {chunk_mb:.2f} MB)\n", flush=True)

        elapsed = time.time() - start_time
        print("\n" + "=" * 60)
        print("               任务全部完成！(--split 分段模式)")
        print(f"  共生成 {len(generated_files)} 个独立 PDF 分卷 (均包含专属封面与目录):")
        for c_name, full_path, c_count, c_mb in generated_files:
            print(f"    * {c_name} (收录 {c_count} 篇, 大小 {c_mb:.2f} MB) -> {full_path}")
        print(f"  总耗时: {elapsed:.1f} 秒")
        print("=" * 60)

    else:
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
