# SCP 档案下载与高保真 PDF 整合工具

用于从本地 Kiwix 或在线镜像站点抓取 SCP 基金会档案，并构建高保真移动端 PDF 电子书的自动化工具。支持 SCP-001 全量多提案及 SCP-002 至 SCP-9999 的批量抓取、结构化清洗、版心排版、原生多级大纲注入与 LaTeX 源码导出。

---

## 核心特性

- **完整支持 SCP-001 多提案**：自动解析 001 枢纽页，全量收录本地镜像中的全部 001 提案。
- **高保真移动端排版**：严格匹配 OnePlus 3 版心规格与 9.96pt 正文字号，每行稳定容纳 25~26 个汉字。
- **动态页眉与页脚**：正文页安全区自动绘制 0.4pt 页眉横线，奇数页居右、偶数页居左严密贴边；底部中央独立连续计算正文页码；封面与目录绝对无页码。
- **原生多级书签树**：自动生成 PDF 目录大纲（TOC Bookmarks），SCP-001 提案以二级折叠树形式呈现，点击可精确跳转到对应页面。
- **断点缓存与容错**：页面与图片自动缓存至本地；遇到 404 或无离线副本的外链自动平滑跳过；自动清理失效或损坏的图片标签，杜绝 PDF 中出现破损图标。
- **图像自动压缩**：下载配图自动通过 Pillow 转换为 JPEG 并以 60% 质量压缩，大幅减少最终 PDF 体积。
- **LaTeX 源码导出**：使用 `--export-tex` 可将全部条目导出为 `.tex` 文件到根目录下的 `tex/` 文件夹，并自动生成 `tex/index.tex` 索引。

---

## 环境要求

- Python 3.10+
- 依赖库安装：
  ```bash
  pip install playwright pymupdf beautifulsoup4 pillow jinja2
  playwright install chromium
  ```

## 快速使用

### 1. 默认完整构建
从镜像站抓取 SCP-001 枢纽页、全部 001 提案以及 SCP-002 至 SCP-200，并生成 PDF：
```bash
python main.py
```

### 2. 使用本地缓存快速重编
若已下载 HTML 与图片，可添加 `--skip-download` 跳过网络抓取，直接利用本地磁盘缓存编译：
```bash
python main.py --skip-download
```

### 3. 指定抓取编号范围与输出路径
```bash
python main.py --start 2 --end 100 -o scp_002_100.pdf
```

### 4. 仅收录常规条目（不包含 001 提案）
```bash
python main.py --no-001 --start 2 --end 200
```

### 5. 分段生成独立分卷 (--split [N])
每隔 N 个 SCP 条目自动切分为一个独立的 PDF 分卷文件（各分卷均包含独立的专属封面与目录），文件自动命名为 `scp_xxx-xxx.pdf`（此时 `--output` 参数自动失效）。不指定 N 时默认每 200 篇切分一次：
```bash
# 每 200 篇切分（默认）
python main.py --start 1 --end 600 --split
# 每 100 篇切分
python main.py --start 1 --end 600 --split 100
```

### 6. 导出 LaTeX 源码 (--export-tex)
将全部解析好的条目导出为 `.tex` 文件到根目录 `tex/` 文件夹，并生成 `tex/index.tex` 索引：
```bash
python main.py --export-tex
```

---

## 命令行参数说明

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `--start` | 常规条目起始编号（`1` 时同时收录 SCP-001 枢纽页与全部提案） | `1` |
| `--end` | 常规条目结束编号 | `200` |
| `--host`, `--base-url` | SCP 镜像站点基地址（需以 `scp-` 结尾） | `http://192.168.6.138:8080/viewer#...` |
| `--output`, `-o` | 输出 PDF 文件名或绝对路径（`--split` 开启时无效） | `scp.op3.v1.20_scp001-200.pdf` |
| `--split [N]` | 启用分段模式（每隔 N 个条目生成独立 PDF，命名为 `scp_xxx-xxx.pdf`，不传 N 默认 200） | 关闭 |
| `--skip-download` | 跳过网络抓取，直接使用本地缓存 | 关闭 |
| `--export-tex` | 将全部条目导出为 `.tex` 文件到 `tex/` 目录 | 关闭 |

---

## 目录结构

```text
scp-downloader/
├── crawler.py           # 网络下载与断点缓存模块
├── parser.py            # HTML 清洗与结构化解析模块
├── pdf_builder.py       # Playwright 版心排版与 PyMuPDF 后处理
├── tex_exporter.py      # LaTeX 源码导出模块
├── main.py              # 主程序入口
├── logo.png             # SCP Logo（可选，封面使用）
├── templates/
│   ├── op3_book.html    # Jinja2 电子书排版模板
│   └── op3_style.css    # 版心排版与正文字体 CSS 样式表
├── data/
│   ├── html/            # 本地 HTML 缓存（含 proposals 子目录）
│   ├── images/          # 本地已压缩图片缓存
│   └── logo_b64.txt     # logo base64 缓存（自动生成）
└── tex/                 # LaTeX 导出目录（--export-tex 时自动生成）
    ├── 001.tex
    ├── 002.tex
    └── index.tex
```

## 致谢

[7sDream/scp-pdf](https://github.com/7sDream/scp-pdf) 参照其项目的PDF样式

