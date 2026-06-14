import os
import re
import uuid
import asyncio
import logging
from typing import Optional

import aiofiles
import aiohttp
import yt_dlp
from bs4 import BeautifulSoup

from config import DOWNLOAD_DIR

logger = logging.getLogger(__name__)


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip().strip(".")
    if len(name) > 100:
        name = name[:100]
    return name or "pinterest_media"


def _get_original_image_url(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    for prop in ("og:image", "twitter:image", "og:image:secure_url"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            url = tag["content"]
            url = re.sub(r"/\d+x/", "/originals/", url)
            url = re.sub(r"/\d+x\d+/", "/originals/", url)
            url = re.sub(r"_?\d+x\d+\.", ".", url)
            return url
    return None


def _get_page_title(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "title"})
    if title_tag and title_tag.get("content"):
        return title_tag["content"]
    tag = soup.find("title")
    if tag and tag.text:
        return tag.text.split(" | ")[0].split(" — ")[0].strip()
    return None


async def download_pinterest(url: str, max_height: Optional[int] = None) -> Optional[dict]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    result = await _try_ytdlp(url, max_height)
    if result:
        return result
    logger.info("yt-dlp failed, trying image fallback...")
    return await _fallback_image(url)


async def _try_ytdlp(url: str, max_height: Optional[int] = None) -> Optional[dict]:
    loop = asyncio.get_event_loop()

    def sync() -> Optional[dict]:
        uid = uuid.uuid4().hex[:12]
        outtmpl = os.path.join(DOWNLOAD_DIR, f"%(id)s_{uid}.%(ext)s")

        height_filter = f"[height<={max_height}]" if max_height else ""
        fmt = f"bestvideo{height_filter}+bestaudio/best{height_filter}/best"

        ydl_opts = {
            "format": fmt,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 14) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.6422.165 Mobile Safari/537.36"
                ),
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = _sanitize_filename(info.get("title", "media"))
                for f in os.listdir(DOWNLOAD_DIR):
                    if uid in f:
                        fpath = os.path.join(DOWNLOAD_DIR, f)
                        ext = f.rsplit(".", 1)[-1]
                        return {
                            "filepath": fpath,
                            "filename": f"{title}.{ext}",
                            "filesize": os.path.getsize(fpath),
                            "file_type": "video",
                        }
                return None
        except Exception as e:
            logger.info("yt-dlp failed: %s", e)
            return None

    return await loop.run_in_executor(None, sync)


async def _fallback_image(url: str) -> Optional[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/125.0.6422.165 Mobile Safari/537.36",
    }
    conn = aiohttp.TCPConnector(force_close=True)

    async with aiohttp.ClientSession(headers=headers, connector=conn, timeout=aiohttp.ClientTimeout(total=90)) as session:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.warning("HTTP %d fetching pin page", resp.status)
                    return None
                html = await resp.text()
        except Exception as e:
            logger.warning("Failed to fetch pin page: %s", e)
            return None

    img_url = _get_original_image_url(html)
    if not img_url:
        logger.warning("No image URL found in page")
        return None

    title = _get_page_title(html)
    safe_title = _sanitize_filename(title or f"pinterest_{uuid.uuid4().hex[:8]}")

    ext = img_url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
    fname = f"{safe_title}.{ext}"
    fpath = os.path.join(DOWNLOAD_DIR, fname)

    conn2 = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(headers=headers, connector=conn2, timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.get(img_url) as resp:
                if resp.status != 200:
                    logger.warning("HTTP %d downloading image", resp.status)
                    return None
                async with aiofiles.open(fpath, "wb") as f:
                    await f.write(await resp.read())
                return {
                    "filepath": fpath,
                    "filename": fname,
                    "filesize": os.path.getsize(fpath),
                    "file_type": "photo",
                }
        except Exception as e:
            logger.warning("Failed to download image: %s", e)
            return None
