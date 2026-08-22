# Scanned PDF to Epub Converter

[English](README.md) | [中文](README_zh.md)

This tool converts scanned PDF books into clean, readable EPUB ebooks using the Baidu PaddleOCR-VL 1.6 Layout Analysis Jobs API.

## Features

- **High-Quality Layout Analysis**: Powered by PaddleOCR-VL 1.6 (OmniDocBench 96.3% SOTA, PP-DocLayoutV3) to intelligently detect paragraphs, headers, images, and tables.
- **Asynchronous Cloud Processing**: Uses the PaddleOCR v2 Jobs API for native multi-page processing with real-time progress updates (`extractedPages / totalPages`).
- **Smart Chapter Splitting**: Automatically detects chapter headings from OCR output. An interactive TOC review lets you confirm, remove, or adjust detected chapters before generating the EPUB.
- **Cover Image**: Automatically extracts the first page of the PDF as the EPUB cover.
- **Metadata Support**: Interactively prompts for book title and author based on OCR'd first-page text, or accepts them via CLI arguments.
- **Image Embedding**: Preserves and embeds images from the original PDF into the EPUB.
- **Clean Output**: Removes headers, footers, and page numbers, and reflows split sentences for a seamless reading experience.
- **Robust Checkpointing**: Saves job ID and result JSON locally. If interrupted, simply re-run to resume immediately.

## Prerequisites

- Python 3.8+
- PaddleOCR API Token

## Getting an API Token

1. Log in to [Baidu AIStudio (飞桨星河社区)](https://aistudio.baidu.com/).
2. Go to the **PaddleOCR** / **PaddleOCR-VL-1.6** model or application section.
3. Copy your private **API Token** from your user profile or API dashboard.

## Installation & Usage (Recommended: `uv`)

This project uses [uv](https://github.com/astral-sh/uv) for fast, reliable dependency management.

1. **Clone the repository**:

    ```bash
    git clone https://github.com/yourusername/pdf2epub-paddle.git
    cd pdf2epub-paddle
    ```

2. **Install `uv`** (if you haven't already):

    ```bash
    # On macOS/Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

3. **Set up your API token**:

    ```bash
    cp .env.example .env
    ```

    Edit `.env` and add your token:

    ```env
    PADDLE_API_TOKEN=your_api_token_here
    # Optional override:
    # PADDLE_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
    # PADDLE_MODEL=PaddleOCR-VL-1.6
    ```

4. **Run directly with `uv`**:

    ```bash
    uv run pdf2epub_paddle.py /path/to/your/book.pdf
    ```

    The tool will submit the PDF as an OCR job, show real-time progress, display OCR'd text from the first page, prompt for metadata, and review TOC chapters.

### CLI Options

- **Provide Metadata Directly**:
  ```bash
  uv run pdf2epub_paddle.py --title "Book Title" --author "Author Name" /path/to/your/book.pdf
  ```

- **Custom Output Path**:
  ```bash
  uv run pdf2epub_paddle.py --output /path/to/output.epub /path/to/your/book.pdf
  ```

- **Skip Interactive TOC Review (Auto-detect)**:
  ```bash
  uv run pdf2epub_paddle.py --auto-toc /path/to/your/book.pdf
  ```

- **Single Chapter EPUB (No TOC splitting)**:
  ```bash
  uv run pdf2epub_paddle.py --no-toc /path/to/your/book.pdf
  ```

### Global Command Support

You can run the tool from anywhere using the `epub` command:

1. **Setup**: Create a symbolic link to the `epub` script in your PATH:

    ```bash
    sudo ln -s "$(pwd)/epub" /usr/local/bin/epub
    ```

2. **Usage**:

    ```bash
    epub /path/to/your/book.pdf
    ```

## License

[MIT License](LICENSE)
