import os
import sys
import argparse
import base64
import json
import re
import tempfile
import time
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter
from difflib import SequenceMatcher

import requests
import fitz  # PyMuPDF
from ebooklib import epub
from dotenv import load_dotenv

# Load .env file if present (does not override existing env vars)
load_dotenv()

# --- Configuration ---
DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"

JOB_URL = os.getenv("PADDLE_API_URL", DEFAULT_JOB_URL)
API_TOKEN = os.getenv("PADDLE_API_TOKEN", "")
MODEL = os.getenv("PADDLE_MODEL", DEFAULT_MODEL)

MAX_DAILY_PAGES = 3000
POLL_INTERVAL_SECONDS = 5


def check_dependencies():
    """Checks if required libraries are installed."""
    missing = []
    try:
        import fitz
    except ImportError:
        missing.append("pymupdf")
    try:
        import ebooklib
    except ImportError:
        missing.append("EbookLib")

    if missing:
        print(f"[!] Missing dependencies: {', '.join(missing)}")
        print(f"    Please run: pip install {' '.join(missing)}")
        return False
    return True


def submit_ocr_job(
    file_path: str,
    token: str,
    job_url: str = JOB_URL,
    model: str = MODEL,
    optional_payload: Optional[Dict] = None,
) -> Optional[str]:
    """
    Submits a PDF or document file to the PaddleOCR-VL 1.6 Jobs API.
    Returns the jobId if successful, or None on failure.
    """
    if not os.path.exists(file_path):
        print(f"[!] Error: File not found at {file_path}")
        return None

    if optional_payload is None:
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }

    headers = {
        "Authorization": f"bearer {token}",
    }
    data = {
        "model": model,
        "optionalPayload": json.dumps(optional_payload),
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"[*] Submitting OCR job ({model}) for '{os.path.basename(file_path)}'...")
            with open(file_path, "rb") as f:
                files = {"file": f}
                resp = requests.post(
                    job_url, headers=headers, data=data, files=files, timeout=120
                )

            if resp.status_code != 200:
                print(
                    f"[!] Job submission failed (status {resp.status_code}): {resp.text}"
                )
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                return None

            res_json = resp.json()
            if res_json.get("code") != 0 and "data" not in res_json:
                print(f"[!] Job submission error: {res_json.get('msg', 'Unknown error')}")
                return None

            job_id = str(res_json.get("data", {}).get("jobId"))
            print(f"[+] Job submitted successfully! Job ID: {job_id}")
            return job_id

        except Exception as e:
            print(
                f"[!] Connection error during job submission (attempt {attempt + 1}/{max_retries}): {e}"
            )
            if attempt < max_retries - 1:
                time.sleep(3)

    return None


def poll_ocr_job(
    job_id: str,
    token: str,
    job_url: str = JOB_URL,
    poll_interval: int = POLL_INTERVAL_SECONDS,
) -> Optional[str]:
    """
    Polls the PaddleOCR Job API until processing is complete.
    Returns the jsonUrl string pointing to the JSONL results on success.
    """
    headers = {
        "Authorization": f"bearer {token}",
    }
    print(f"[*] Polling OCR job status (Job ID: {job_id})...")

    last_status = None
    while True:
        try:
            resp = requests.get(f"{job_url}/{job_id}", headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"[!] Polling HTTP error {resp.status_code}: {resp.text}")
                time.sleep(poll_interval)
                continue

            data = resp.json().get("data", {})
            state = data.get("state")

            if state == "pending":
                if last_status != "pending":
                    print("    Status: Pending in queue...")
                    last_status = "pending"
            elif state == "running":
                progress = data.get("extractProgress", {})
                total_pages = progress.get("totalPages")
                extracted_pages = progress.get("extractedPages")
                if total_pages is not None and extracted_pages is not None:
                    print(
                        f"    Status: Processing page {extracted_pages}/{total_pages}..."
                    )
                else:
                    if last_status != "running":
                        print("    Status: Running...")
                        last_status = "running"
            elif state == "done":
                progress = data.get("extractProgress", {})
                extracted_pages = progress.get("extractedPages", 0)
                start_time = progress.get("startTime", "")
                end_time = progress.get("endTime", "")
                print(
                    f"[+] Job completed! Successfully extracted {extracted_pages} pages ({start_time} to {end_time})"
                )
                json_url = data.get("resultUrl", {}).get("jsonUrl")
                return json_url
            elif state == "failed":
                error_msg = data.get("errorMsg", "Unknown failure")
                print(f"[!] OCR Job failed: {error_msg}")
                return None
            else:
                print(f"    Status: {state}...")

            time.sleep(poll_interval)

        except Exception as e:
            print(
                f"[!] Network error during polling: {e}. Retrying in {poll_interval}s..."
            )
            time.sleep(poll_interval)


def fetch_job_results(json_url: str) -> List[Dict]:
    """
    Downloads the JSONL result file from the BOS storage URL and parses each line.
    """
    print("[*] Downloading parsing results from cloud storage...")
    resp = requests.get(json_url, timeout=60)
    resp.raise_for_status()

    results = []
    lines = resp.text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if "result" in parsed:
                results.append(parsed)
            else:
                results.append({"result": parsed})
        except Exception as e:
            print(f"[!] Warning: Failed to parse JSONL line: {e}")

    print(f"[+] Successfully loaded {len(results)} page result block(s).")
    return results


def extract_cover_image(pdf_path: str, output_path: str) -> Optional[str]:
    """Renders the first page of a PDF as a PNG image for use as an EPUB cover."""
    doc = fitz.open(pdf_path)
    try:
        if len(doc) == 0:
            print("[!] PDF has no pages; skipping cover extraction.")
            return None
        page = doc.load_page(0)
        mat = fitz.Matrix(2, 2)  # 2x zoom (~144 DPI)
        pix = page.get_pixmap(matrix=mat)
        pix.save(output_path)
        print(f"[*] Cover image extracted to {output_path}")
        return output_path
    except Exception as e:
        print(f"[!] Failed to extract cover image: {e}")
        return None
    finally:
        doc.close()


def extract_metadata_interactive(
    results: List[Dict], default_title: str
) -> Dict[str, Optional[str]]:
    """Shows the first page OCR text and prompts the user for title and author."""
    first_page_text = ""
    try:
        first_page_text = results[0]["result"]["layoutParsingResults"][0]["markdown"][
            "text"
        ]
    except (IndexError, KeyError, TypeError):
        pass

    if first_page_text:
        print("\n--- First page OCR text ---")
        print(first_page_text.strip())
        print("----------------------------\n")
    else:
        print("\n[!] Could not extract text from the first page.\n")

    title = input(
        f"Enter book title (or press Enter to use '{default_title}'): "
    ).strip()
    if not title:
        title = default_title

    author = input("Enter author name (or press Enter to skip): ").strip()

    return {"title": title, "author": author if author else None}


# ==========================================
# --- Smart Table of Contents (TOC) Engine ---
# ==========================================

def normalize_title(s: str) -> str:
    """Normalizes title string by stripping markdown markup, Roman numerals, and punctuation."""
    s = re.sub(r"^#+\s*", "", s.strip())
    s = re.sub(r"^[IVXLCDM0-9\.\s\-–—:]+", "", s, flags=re.I)
    s = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]", "", s).lower()
    return s


def is_major_chapter_title(title: str) -> bool:
    """Checks if a title represents a top-level major chapter."""
    clean = title.strip()
    return bool(
        re.match(
            r"^(?:[IVXLCDM]+(?:\.|\b)|\b(?:Chapter|Part|Book|Section|Lecture|Preface|Introduction|Epilogue|Conclusion|Appendix|Index|Prologue|Foreword|Afterword)\b|第[零一二三四五六七八九十百千0-9]+[篇章节讲]|序|前言|导论|后记|附录|索引)",
            clean,
            re.I,
        )
    )


def extract_printed_toc(results: List[Dict]) -> Optional[List[Dict]]:
    """
    Tier 1: Detects and parses the printed Table of Contents (TOC) page in the front-matter
    and matches entries against the document body for exact split pages.
    """
    toc_entries = []
    contents_pages = set()

    # 1. Search first 25 pages for printed Contents / Table of Contents / 目录
    for p_idx, r in enumerate(results[:25]):
        for res in r.get("result", {}).get("layoutParsingResults", []):
            md = res["markdown"]["text"]
            lines = [l.strip() for l in md.split("\n") if l.strip()]
            has_contents_header = any(
                re.match(
                    r"^#{1,3}\s*(?:Contents|Table of Contents|TABLE OF CONTENTS|目录|目\s*录)\b",
                    l,
                    re.I,
                )
                for l in lines
            )
            if has_contents_header:
                contents_pages.add(p_idx)
                for line in lines:
                    if re.match(
                        r"^#{1,3}\s*(?:Contents|Table of Contents|TABLE OF CONTENTS|目录|目\s*录)\b",
                        line,
                        re.I,
                    ):
                        continue

                    # TOC entries typically end with a page number or start with a major chapter identifier
                    has_page_num = bool(re.search(r"\s+\d+$", line))
                    is_major = is_major_chapter_title(line)

                    if not (has_page_num or is_major):
                        continue  # Skip epigraphs, poems, notes on TOC page

                    clean = re.sub(r"\s+\d+$", "", line).strip()
                    clean = re.sub(r"\$\s*\\underline\{(.+?)\}\s*\$", r"\1", clean).strip()
                    clean = (
                        re.sub(r"^[-*•\d\.\s]+\s+", "", clean).strip()
                        if clean.startswith(("-", "*", "•"))
                        else clean
                    )
                    if len(clean) >= 3 and not clean.isdigit():
                        toc_entries.append(
                            {
                                "title": clean,
                                "is_major": is_major,
                                "level": 1 if is_major else 2,
                            }
                        )

    if len(toc_entries) < 3:
        return None

    # 2. Match each entry to body pages
    matched = []
    start_search_page = max(contents_pages) + 1 if contents_pages else 0
    last_page = start_search_page

    for entry in toc_entries:
        target = normalize_title(entry["title"])
        if not target:
            continue
        best_score = 0
        best_page = 0
        best_match_text = None

        search_start = max(start_search_page, last_page - 1)
        for p_idx in range(search_start, len(results)):
            for res in results[p_idx].get("result", {}).get("layoutParsingResults", []):
                page_md = res["markdown"]["text"]
                for line in page_md.split("\n"):
                    line_clean = line.strip()
                    if not line_clean:
                        continue
                    line_norm = normalize_title(line_clean)
                    if not line_norm:
                        continue

                    ratio = SequenceMatcher(None, target, line_norm).ratio()
                    if target in line_norm or ratio > 0.8:
                        score = ratio + (0.3 if line_clean.startswith("#") else 0)
                        if score > best_score:
                            best_score = score
                            best_match_text = line_clean
                            best_page = p_idx + 1
                            if best_score >= 1.2:
                                break
            if best_score >= 1.2:
                break

        if best_page > 0 and best_score >= 0.8:
            last_page = max(last_page, best_page)
            matched.append(
                {
                    "title": entry["title"],
                    "page": best_page,
                    "level": entry["level"],
                    "is_major": entry["is_major"],
                    "matched_text": best_match_text,
                }
            )

    return matched if len(matched) >= 3 else None


def extract_and_filter_body_headings(
    results: List[Dict], book_title: Optional[str] = None
) -> List[Dict]:
    """
    Tier 2 Fallback: Scans document body for headings with intelligent filtering
    against repeating running headers, publisher noise, and generic templates.
    """
    any_header_pattern = re.compile(r"^(#{1,3})\s+(.+)$")
    latex_pattern = re.compile(r"\$\s*\\underline\{(.+?)\}\s*\$")
    candidates = []
    global_page = 0

    # Publisher / front-matter blacklist
    noise_patterns = re.compile(
        r"^(?:PENGUIN BOOKS|HARPERCOLLINS|ROUTLEDGE|SPRINGER|OXFORD UNIVERSITY PRESS|"
        r"CAMBRIDGE UNIVERSITY PRESS|SIMON & SCHUSTER|VINTAGE|MACMILLAN|WILEY|"
        r"Contents|Table of Contents|TABLE OF CONTENTS|目录|目\s*录|"
        r"ISBN\b|Copyright\b|All rights reserved|Printed in|Published by)\b",
        re.I,
    )

    norm_book_title = normalize_title(book_title) if book_title else ""

    for result in results:
        if not result or "result" not in result:
            continue
        for page_res in result["result"].get("layoutParsingResults", []):
            global_page += 1
            page_md = page_res["markdown"]["text"]
            for line in page_md.split("\n"):
                if line.strip().isdigit():
                    continue
                match = any_header_pattern.match(line)
                if match:
                    title = match.group(2).strip()
                    title = latex_pattern.sub("", title).strip()
                    title = re.sub(r"^\d+\s+", "", title).strip()

                    if len(title) < 3 or noise_patterns.match(title):
                        continue

                    candidates.append(
                        {
                            "title": title,
                            "page": global_page,
                            "level": len(match.group(1)),
                            "md_line": line,
                        }
                    )

    if not candidates:
        return []

    # Count frequencies of normalized titles to identify running headers
    title_counts = Counter(normalize_title(c["title"]) for c in candidates)

    filtered = []
    for c in candidates:
        norm = normalize_title(c["title"])
        # Filter if repeated >= 2 times (running header or repeating template)
        if title_counts[norm] >= 2:
            continue
        # Filter if matches book title
        if norm_book_title and norm == norm_book_title:
            continue
        # Filter front-matter noise on first 4 pages
        if c["page"] <= 4 and (len(c["title"]) > 60 or noise_patterns.search(c["title"])):
            continue

        is_major = is_major_chapter_title(c["title"]) or c["level"] == 1
        filtered.append(
            {
                "title": c["title"],
                "page": c["page"],
                "level": 1 if is_major else 2,
                "is_major": is_major,
            }
        )

    return filtered


def detect_chapter_headings(
    results: List[Dict], book_title: Optional[str] = None
) -> Tuple[List[Dict], List[Dict], str]:
    """
    Dual-Tier TOC Engine:
    1. Tries to extract the printed Table of Contents from front-matter.
    2. Falls back to body scanning with noise suppression.
    Returns (major_headings, all_headings, source_str).
    """
    # Tier 1: Printed TOC
    printed_matched = extract_printed_toc(results)
    if printed_matched:
        major = [h for h in printed_matched if h["is_major"]]
        if len(major) < 2:
            major = printed_matched
        return major, printed_matched, "Printed Table of Contents"

    # Tier 2: Body scanning fallback
    body_headings = extract_and_filter_body_headings(results, book_title)
    major = [h for h in body_headings if h["is_major"]]
    if len(major) < 3:
        major = body_headings
    return major, body_headings, "Body Heading Scanner"


def review_toc_interactive(
    major_headings: List[Dict],
    all_headings: List[Dict],
    source: str = "Printed Table of Contents",
) -> List[Dict]:
    """
    Interactive prompt for reviewing and editing the detected TOC.
    Offers major chapters as default and sub-sections via 'all'.
    """
    has_subsections = len(all_headings) > len(major_headings)
    active_headings = list(major_headings)
    viewing_all = False

    def _print_heading_list(headings):
        print(f"  {'#':>3}  | {'Pg':>4} | Level | Heading")
        print(f"  {'---':>3}--+------+-------+{'-' * 44}")
        for i, h in enumerate(headings):
            lvl_str = "H1" if h["level"] == 1 else "  H2"
            indent = "" if h["level"] == 1 else "  "
            print(f"  {i+1:>3}  | {h['page']:>4} |  {lvl_str}  | {indent}{h['title']}")

    def _show_options():
        print()
        print("Options:")
        if has_subsections and not viewing_all:
            print(
                f"  [Enter]    Accept Major Chapters only ({len(major_headings)} chapters)"
            )
            print(
                f"  all        Show ALL {len(all_headings)} chapters and sub-sections"
            )
        else:
            print(f"  [Enter]    Accept current list ({len(active_headings)} chapters)")
            if has_subsections and viewing_all:
                print(f"  major      Show only Major Chapters ({len(major_headings)} chapters)")

        print("  1,3,5      Remove headings by number (comma-separated)")
        print("  +1,3,5     Keep ONLY these headings (comma-separated)")
        print("  none       No chapters (entire book as single chapter)")
        print()

    print(
        f"\n--- Detected Chapter Headings (Source: {source} | {len(major_headings)} Major Chapters, {len(all_headings)} Total) ---"
    )
    _print_heading_list(active_headings)
    _show_options()

    while True:
        choice = input("Your choice: ").strip()

        if choice == "":
            return list(active_headings)
        elif choice.lower() == "none":
            return []
        elif choice.lower() == "all" and has_subsections and not viewing_all:
            active_headings = list(all_headings)
            viewing_all = True
            print(f"\n--- All Headings ({len(active_headings)} chapters & sub-sections) ---")
            _print_heading_list(active_headings)
            _show_options()
            continue
        elif choice.lower() == "major" and has_subsections and viewing_all:
            active_headings = list(major_headings)
            viewing_all = False
            print(f"\n--- Major Chapters ({len(active_headings)}) ---")
            _print_heading_list(active_headings)
            _show_options()
            continue
        else:
            try:
                keep_mode = choice.startswith("+")
                if keep_mode:
                    choice = choice[1:]

                nums = set()
                valid = True
                for part in choice.split(","):
                    part = part.strip().lstrip("-")
                    num = int(part)
                    if 1 <= num <= len(active_headings):
                        nums.add(num)
                    else:
                        print(
                            f"  [!] Invalid number: {num} (must be 1-{len(active_headings)})"
                        )
                        valid = False
                        break

                if not valid:
                    continue

                if keep_mode:
                    confirmed = [
                        h for i, h in enumerate(active_headings) if (i + 1) in nums
                    ]
                else:
                    confirmed = [
                        h for i, h in enumerate(active_headings) if (i + 1) not in nums
                    ]

                if confirmed:
                    print(f"\nUpdated TOC ({len(confirmed)} chapters):")
                    _print_heading_list(confirmed)
                else:
                    print("\n  All headings removed. Book will be a single chapter.")

                confirm = input("\nConfirm? [Y/n]: ").strip().lower()
                if confirm in ("", "y", "yes"):
                    return confirmed
                else:
                    print("\n--- Current Chapter Headings ---")
                    _print_heading_list(active_headings)
                    _show_options()
                    continue

            except ValueError:
                print(
                    "  [!] Invalid input. Use numbers like '1,3,5' or '+1,3,5' for keep mode, or 'all'/'major'."
                )
                continue


def find_matching_heading(
    line_text: str, confirmed_headings: List[Dict]
) -> Optional[Dict]:
    """Finds if a line matches any of the confirmed headings via normalized fuzzy comparison."""
    norm_line = normalize_title(line_text)
    if not norm_line:
        return None
    for h in confirmed_headings:
        norm_h = normalize_title(h["title"])
        if norm_line == norm_h or (
            len(norm_line) >= 5
            and SequenceMatcher(None, norm_line, norm_h).ratio() > 0.85
        ):
            return h
    return None


def download_image(url: str, save_path: str, max_retries: int = 3) -> bool:
    """Downloads an image from a URL to a local path with retry logic."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(response.content)
                return True
            else:
                print(
                    f"[!] Image download HTTP {response.status_code} (attempt {attempt + 1}/{max_retries}): {os.path.basename(save_path)}"
                )
        except Exception as e:
            print(
                f"[!] Image download failed (attempt {attempt + 1}/{max_retries}): {os.path.basename(save_path)} - {e}"
            )
        if attempt < max_retries - 1:
            wait = 2**attempt * 2
            print(f"    Retrying in {wait}s...")
            time.sleep(wait)
    print(f"[!] PERMANENT FAILURE downloading: {os.path.basename(save_path)}")
    return False


def verify_all_images_downloaded(results: List[Dict], image_dir: str) -> tuple:
    """Verifies all API-referenced images exist on disk."""
    total = 0
    missing = []
    for result in results:
        if not result or "result" not in result:
            continue
        for page_res in result["result"].get("layoutParsingResults", []):
            images_map = page_res["markdown"].get("images", {})
            for rel_path, img_url in images_map.items():
                total += 1
                local_path = os.path.join(image_dir, rel_path)
                if not os.path.exists(local_path):
                    missing.append((rel_path, img_url))
    return total, missing


def create_epub(
    title: str,
    results: List[Dict],
    output_file: str,
    image_dir: str,
    cover_image_path: Optional[str] = None,
    author: Optional[str] = None,
    confirmed_headings: Optional[List[Dict]] = None,
):
    """
    Creates an EPUB file from the aggregated API results.
    """
    book = epub.EpubBook()
    book.set_identifier(f"id_{title}")
    book.set_title(title)
    book.set_language("en")

    if author:
        book.add_author(author)

    # Set cover image
    if cover_image_path and os.path.exists(cover_image_path):
        with open(cover_image_path, "rb") as f:
            cover_data = f.read()
        book.set_cover("cover.png", cover_data)

    chapters = []

    # CSS for the book
    style = """
    body { font-family: serif; line-height: 1.8; text-align: justify; margin: 1em; }
    h1 { text-align: center; margin: 1.5em 0 0.8em 0; font-size: 1.6em; }
    h2 { margin: 1.2em 0 0.6em 0; font-size: 1.3em; }
    h3 { margin: 1em 0 0.5em 0; font-size: 1.1em; }
    p { margin-bottom: 0.8em; }
    blockquote { margin: 1em 2em; font-style: italic; }
    img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
    """
    nav_css = epub.EpubItem(
        uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style
    )
    book.add_item(nav_css)

    full_markdown = ""

    for chunk_idx, result in enumerate(results):
        if not result or "result" not in result:
            continue

        layout_results = result["result"].get("layoutParsingResults", [])

        for i, page_res in enumerate(layout_results):
            page_md = page_res["markdown"]["text"]
            images_map = page_res["markdown"].get("images", {})

            for rel_path, img_url in images_map.items():
                local_img_path = os.path.join(image_dir, rel_path)
                if os.path.exists(local_img_path):
                    with open(local_img_path, "rb") as img_f:
                        img_data = img_f.read()

                    epub_img = epub.EpubImage()
                    epub_img.file_name = rel_path
                    epub_img.media_type = "image/jpeg"
                    epub_img.content = img_data

                    if rel_path not in [item.file_name for item in book.get_items()]:
                        book.add_item(epub_img)

            # Paragraph reflowing
            lines = page_md.split("\n")
            cleaned_lines = []
            for line in lines:
                if line.strip().isdigit():
                    continue
                cleaned_lines.append(line)

            reflowed_paragraphs = []
            current_para = ""

            terminal_chars = (".", "!", "?", '"', "'", ")", "]", "}", ":", ";")
            header_pattern = re.compile(r"^(#{1,6}\s|>|\s*[-*]\s|\d+\.)")
            non_join_endings = (":", ";", ",", "-")

            for line in cleaned_lines:
                stripped = line.rstrip()
                if not stripped:
                    if current_para:
                        reflowed_paragraphs.append(current_para)
                        current_para = ""
                    continue

                is_special = header_pattern.match(stripped)

                if is_special:
                    if current_para:
                        reflowed_paragraphs.append(current_para)
                        current_para = ""
                    reflowed_paragraphs.append(stripped)
                elif not current_para:
                    current_para = stripped
                else:
                    current_ends = current_para.rstrip()
                    if current_ends.endswith(terminal_chars):
                        reflowed_paragraphs.append(current_para)
                        current_para = stripped
                    elif current_ends and current_ends[-1] in non_join_endings:
                        reflowed_paragraphs.append(current_para)
                        current_para = stripped
                    elif len(stripped) < 20 and not stripped.endswith(terminal_chars):
                        reflowed_paragraphs.append(current_para)
                        current_para = stripped
                    else:
                        current_para = current_ends + " " + stripped

            if current_para:
                reflowed_paragraphs.append(current_para)

            page_markdown = "\n\n".join(reflowed_paragraphs)

            if full_markdown and page_markdown:
                full_stripped = full_markdown.rstrip()
                page_lines = page_markdown.split("\n")
                first_page_line = page_lines[0].strip() if page_lines else ""

                ends_mid_sentence = full_stripped and not full_stripped.endswith(
                    terminal_chars
                )
                next_is_header = (
                    header_pattern.match(first_page_line) if first_page_line else False
                )

                if ends_mid_sentence and not next_is_header:
                    full_markdown = (
                        full_markdown.rstrip() + " " + page_markdown + "\n\n"
                    )
                else:
                    full_markdown += page_markdown + "\n\n"
            else:
                full_markdown += page_markdown + "\n\n"

    md_lines = full_markdown.split("\n")
    current_chapter_title = "Start"
    current_chapter_content = []
    chapter_count = 0

    major_header_pattern = re.compile(
        r"^(#{1,3})\s+(?:Chapter|Part|Lecture|Preface|Intro|Appendix|Prologue|Epilogue|Conclusion|Book|Acknowledgements|Contents|Abstract|序|前言|导论|目录|第[零一二三四五六七八九十百千0-9]+[篇章讲]).*"
    )
    any_header_pattern = re.compile(r"^(#{1,3})\s+(.+)$")

    for line in md_lines:
        match = any_header_pattern.match(line)
        is_split_point = False
        clean_matched_title = None

        if match:
            heading_text = match.group(2).strip()
            heading_text = re.sub(
                r"\$\s*\\underline\{(.+?)\}\s*\$", "", heading_text
            ).strip()
            heading_text = re.sub(r"^\d+\s+", "", heading_text).strip()

            if confirmed_headings is not None:
                matched_h = find_matching_heading(heading_text, confirmed_headings)
                if matched_h:
                    clean_matched_title = matched_h["title"]
                    if clean_matched_title != current_chapter_title:
                        is_split_point = True
            else:
                is_split_point = bool(major_header_pattern.match(line))
                if is_split_point:
                    clean_matched_title = heading_text

        # Clean up LaTeX artifacts
        line = re.sub(r"\$\s*\^\{(.+?)\}\s*\$", r"", line)
        line = re.sub(r"\$\s*\\underline\{(.+?)\}\s*\$", "", line)

        if match and is_split_point:
            # Check if previous chapter has meaningful content before saving
            content_text = "".join(current_chapter_content).strip()
            if current_chapter_content and len(content_text) > 30:
                safe_title = "".join(
                    [
                        c
                        for c in current_chapter_title
                        if c.isalnum() or c in (" ", "_", "-")
                    ]
                ).strip()
                if not safe_title:
                    safe_title = f"chap_{chapter_count}"

                c = epub.EpubHtml(
                    title=current_chapter_title,
                    file_name=f"{safe_title}_{chapter_count}.xhtml",
                    lang="en",
                )

                try:
                    import markdown

                    html_content = markdown.markdown(
                        "\n".join(current_chapter_content)
                    )
                except ImportError:
                    html_content = (
                        "<p>"
                        + "</p><p>".join(current_chapter_content)
                        + "</p>"
                    )

                c.content = f"<html><head><link rel='stylesheet' href='style/nav.css'/></head><body>{html_content}</body></html>"
                c.add_item(nav_css)
                book.add_item(c)
                chapters.append(c)
                chapter_count += 1

            current_chapter_title = clean_matched_title or match.group(2)
            current_chapter_content = [line]
        else:
            current_chapter_content.append(line)

    # Add last chapter
    if current_chapter_content:
        safe_title = "".join(
            [c for c in current_chapter_title if c.isalnum() or c in (" ", "_", "-")]
        ).strip()
        if not safe_title:
            safe_title = f"chap_{chapter_count}"

        c = epub.EpubHtml(
            title=current_chapter_title,
            file_name=f"{safe_title}_{chapter_count}.xhtml",
            lang="en",
        )
        try:
            import markdown

            html_content = markdown.markdown("\n".join(current_chapter_content))
        except ImportError:
            html_content = "<p>" + "</p><p>".join(current_chapter_content) + "</p>"

        c.content = f"<html><head><link rel='stylesheet' href='style/nav.css'/></head><body>{html_content}</body></html>"
        c.add_item(nav_css)
        book.add_item(c)
        chapters.append(c)

    if not chapters and full_markdown:
        c = epub.EpubHtml(title="Content", file_name="content.xhtml", lang="en")
        try:
            import markdown

            html_content = markdown.markdown(full_markdown)
        except ImportError:
            html_content = "<p>" + "</p><p>".join(full_markdown.split("\n")) + "</p>"
        c.content = f"<html><head><link rel='stylesheet' href='style/nav.css'/></head><body>{html_content}</body></html>"
        book.add_item(c)
        chapters.append(c)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    epub.write_epub(output_file, book, {})
    print(f"[*] EPUB saved to {output_file}")


def main():
    if not check_dependencies():
        return

    parser = argparse.ArgumentParser(
        description="Scanned PDF to EPUB Converter (Powered by PaddleOCR-VL 1.6)"
    )
    parser.add_argument("input_pdf", help="Path to input PDF file")
    parser.add_argument(
        "--output", "-o", help="Path to output EPUB file (default: input_name.epub)"
    )
    parser.add_argument("--title", help="Book title (skips interactive prompt)")
    parser.add_argument("--author", help="Author name (skips interactive prompt)")
    parser.add_argument(
        "--auto-toc",
        action="store_true",
        help="Skip interactive TOC review; use auto-detected headings",
    )
    parser.add_argument(
        "--no-toc",
        action="store_true",
        help="Skip heading detection; produce single-chapter EPUB",
    )
    parser.add_argument(
        "--model",
        default=MODEL,
        help=f"PaddleOCR model name (default: {MODEL})",
    )
    parser.add_argument(
        "--url",
        default=JOB_URL,
        help=f"PaddleOCR Jobs API URL (default: {JOB_URL})",
    )
    args = parser.parse_args()

    input_path = args.input_pdf
    if not os.path.exists(input_path):
        print(f"Error: File {input_path} not found.")
        return

    if args.auto_toc and args.no_toc:
        print("[!] Error: --auto-toc and --no-toc are mutually exclusive.")
        return

    if not API_TOKEN:
        print("[!] Error: PADDLE_API_TOKEN environment variable is not set.")
        print("    Please set it in .env or via: export PADDLE_API_TOKEN='your_token_here'")
        return

    if not args.output:
        args.output = os.path.splitext(input_path)[0] + ".epub"

    # Check total pages
    doc = fitz.open(input_path)
    total_pages = len(doc)
    doc.close()
    print(f"[*] Total pages: {total_pages}")

    if total_pages > MAX_DAILY_PAGES:
        print(
            f"[!] WARNING: This document ({total_pages} pages) exceeds the daily API limit of {MAX_DAILY_PAGES} pages."
        )
        print("    Processing may fail or get blocked if you exceed your quota.")

    # Unique work directory based on filename and size hash
    import hashlib

    file_stat = os.stat(input_path)
    file_sig = f"{os.path.basename(input_path)}_{file_stat.st_size}"
    file_hash = hashlib.md5(file_sig.encode("utf-8")).hexdigest()[:8]
    work_dir = f"paddle_epub_work_{file_hash}"

    print(f"[*] Work directory: {work_dir}")

    image_dir = os.path.join(work_dir, "images")
    os.makedirs(image_dir, exist_ok=True)

    results_file = os.path.join(work_dir, "results.json")
    job_info_file = os.path.join(work_dir, "job_info.json")

    try:
        # Step 1: Extract cover image
        cover_path = extract_cover_image(
            input_path, os.path.join(work_dir, "cover.png")
        )

        # Step 2: OCR Processing via PaddleOCR-VL 1.6 Jobs API
        results = []
        if os.path.exists(results_file):
            print(
                "    [+] Resuming: Found completed OCR results from previous run."
            )
            with open(results_file, "r", encoding="utf-8") as f:
                results = json.load(f)
        else:
            job_id = None
            if os.path.exists(job_info_file):
                try:
                    with open(job_info_file, "r", encoding="utf-8") as f:
                        job_data = json.load(f)
                        job_id = job_data.get("jobId")
                        if job_id:
                            print(
                                f"    [+] Resuming: Found existing job checkpoint (Job ID: {job_id})"
                            )
                except Exception:
                    job_id = None

            if not job_id:
                job_id = submit_ocr_job(
                    input_path, API_TOKEN, job_url=args.url, model=args.model
                )
                if not job_id:
                    print("[!] Failed to submit OCR job. Aborting.")
                    sys.exit(1)
                with open(job_info_file, "w", encoding="utf-8") as f:
                    json.dump({"jobId": job_id}, f)

            json_url = poll_ocr_job(job_id, API_TOKEN, job_url=args.url)
            if not json_url:
                print(
                    "[!] CRITICAL: OCR job did not return a valid result URL. Aborting."
                )
                sys.exit(1)

            results = fetch_job_results(json_url)
            if not results:
                print(
                    "[!] CRITICAL: No results parsed from OCR job output. Aborting."
                )
                sys.exit(1)

            with open(results_file, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

        # Download images immediately
        print("[-] Downloading document images...")
        for res in results:
            layout_results = res.get("result", {}).get(
                "layoutParsingResults", []
            )
            for page_res in layout_results:
                images_map = page_res["markdown"].get("images", {})
                for rel_path, img_url in images_map.items():
                    local_path = os.path.join(image_dir, rel_path)
                    if not os.path.exists(local_path):
                        download_image(img_url, local_path)

        # Step 2.5: Metadata extraction
        default_title = os.path.splitext(os.path.basename(input_path))[0]
        if args.title:
            metadata = {"title": args.title, "author": args.author}
        else:
            metadata = extract_metadata_interactive(results, default_title)

        # Step 2.75: TOC Detection & Review
        if args.no_toc:
            confirmed_headings = []
        elif args.auto_toc:
            major_h, all_h, _ = detect_chapter_headings(results, metadata.get("title"))
            confirmed_headings = major_h
        else:
            print("[-] Step 2.75: Detecting chapter headings...")
            major_h, all_h, src = detect_chapter_headings(
                results, metadata.get("title")
            )
            confirmed_headings = review_toc_interactive(
                major_h, all_h, source=src
            )

        # Step 2.9: Verify all images downloaded
        print("[-] Verifying image downloads...")
        total_imgs, missing_imgs = verify_all_images_downloaded(
            results, image_dir
        )
        if missing_imgs:
            print(
                f"[!] WARNING: {len(missing_imgs)}/{total_imgs} images failed to download."
            )
            print("    Attempting re-download of missing images...")
            still_missing = []
            for rel_path, img_url in missing_imgs:
                local_path = os.path.join(image_dir, rel_path)
                if not download_image(img_url, local_path, max_retries=5):
                    still_missing.append(rel_path)
            if still_missing:
                print(f"\n[!] FAILED to download {len(still_missing)} image(s):")
                for p in still_missing:
                    print(f"    - {p}")
                proceed = (
                    input("\nProceed with EPUB generation anyway? [y/N]: ")
                    .strip()
                    .lower()
                )
                if proceed not in ("y", "yes"):
                    print("[*] Aborted.")
                    sys.exit(1)
            else:
                print(f"    All {total_imgs} images now verified.")
        else:
            print(f"    All {total_imgs} images verified.")

        # Step 3: Generation
        print("[-] Generating EPUB...")
        create_epub(
            metadata["title"],
            results,
            args.output,
            image_dir,
            cover_image_path=cover_path,
            author=metadata["author"],
            confirmed_headings=confirmed_headings,
        )

    finally:
        print(
            f"[*] Done. Intermediate files are in '{work_dir}'. You can delete this folder if verified."
        )


if __name__ == "__main__":
    main()
