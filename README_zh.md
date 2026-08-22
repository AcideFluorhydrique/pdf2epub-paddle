# Scanned PDF to Epub Converter (扫描版 PDF 转 EPUB)

[English](README.md) | [中文](README_zh.md)

本工具利用百度 PaddleOCR-VL 1.6 版面分析 API，将扫描版 PDF 书籍转换为清晰、易读的 EPUB 电子书。

## 功能特性

- **高质量版面分析**：采用 PaddleOCR-VL 1.6（OmniDocBench 96.3% SOTA，PP-DocLayoutV3）智能识别段落、标题、图片、表格及公式。
- **异步任务处理**：使用 PaddleOCR v2 Jobs 异步任务接口，原生支持多页处理与实时进度显示（`extractedPages / totalPages`）。
- **智能章节分割**：自动检测各级标题，并提供交互式目录（TOC）确认与调整机制。
- **自动封面提取**：自动将 PDF 首页渲染提取为 EPUB 封面图片。
- **元数据支持**：根据第一页 OCR 结果交互式提示输入书名与作者，或通过命令行参数指定。
- **保留并嵌入图片**：完整保留原文档中的图片并自动嵌入 EPUB。
- **纯净阅读体验**：自动过滤页眉、页脚与页码，重排跨行截断语句。
- **断点续传**：自动保存 Job ID 与识别结果。如遇中断，再次运行可直接从检查点继续。

## 前置要求

- Python 3.8+
- PaddleOCR API Token（从 [百度飞桨星河社区 (AIStudio)](https://aistudio.baidu.com/) 获取）

## 安装与使用 (推荐使用 `uv`)

本项目使用 [uv](https://github.com/astral-sh/uv) 进行快速、可靠的依赖管理。

1. **克隆仓库**：

    ```bash
    git clone https://github.com/yourusername/pdf2epub-paddle.git
    cd pdf2epub-paddle
    ```

2. **配置 API Token**：

    ```bash
    cp .env.example .env
    ```

    编辑 `.env` 文件填入你的 Token：

    ```env
    PADDLE_API_TOKEN=你的api_token
    # 可选自定义：
    # PADDLE_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
    # PADDLE_MODEL=PaddleOCR-VL-1.6
    ```

3. **使用 `uv` 运行**：

    ```bash
    uv run pdf2epub_paddle.py /path/to/your/book.pdf
    ```

### 命令行参数

- **直接指定元数据**：
  ```bash
  uv run pdf2epub_paddle.py --title "书名" --author "作者名" /path/to/your/book.pdf
  ```

- **指定输出路径**：
  ```bash
  uv run pdf2epub_paddle.py --output /path/to/output.epub /path/to/your/book.pdf
  ```

- **跳过交互式目录确认（全自动目录分割）**：
  ```bash
  uv run pdf2epub_paddle.py --auto-toc /path/to/your/book.pdf
  ```

- **单章节模式（不拆分章节）**：
  ```bash
  uv run pdf2epub_paddle.py --no-toc /path/to/your/book.pdf
  ```

### 全局命令支持

你可通过创建符号链接在任意目录下直接使用 `epub` 命令：

```bash
sudo ln -s "$(pwd)/epub" /usr/local/bin/epub
epub /path/to/your/book.pdf
```

## 开源协议

[MIT License](LICENSE)
