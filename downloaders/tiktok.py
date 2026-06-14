import os
import re
import uuid
import asyncio
import logging
from typing import Optional

import aiofiles
import aiohttp
import yt_dlp

from config import DOWNLOAD_DIR

logger = logging.getLogger(__name__)

TIKTOK_REGEX = re.compile(
    r"(?:https?://)?(?:[^/]+\.)?(?:tiktok\.com|vm\.tiktok\.com)/",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.6422.165 Mobile Safari/537.36"
    ),
}


def is_tiktok_url(text: str) -> bool:
    return bool(TIKTOK_REGEX.search(text))


def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip().strip(".")
    if len(name) > 80:
        name = name[:80]
    return name or "tiktok"


async def download_tiktok(url: str, max_height: Optional[int] = None) -> Optional[dict]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    result = await _try_ytdlp(url, max_height)
    if result:
        return result

    logger.info("yt-dlp failed, trying ssstik.io fallback...")
    return await _try_ssstik(url)


async def _try_ytdlp(url: str, max_height: Optional[int] = None) -> Optional[dict]:
    loop = asyncio.get_event_loop()

    def sync() -> Optional[dict]:
        uid = uuid.uuid4().hex[:12]
        outtmpl = os.path.join(DOWNLOAD_DIR, f"tiktok_%(id)s_{uid}.%(ext)s")

        height_filter = f"[height<={max_height}]" if max_height else ""
        fmt = f"best{height_filter}/best"

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "http_headers": {
                **HEADERS,
                "Referer": "https://www.tiktok.com/",
            },
            "extractor_args": {"tiktok": {"video": "1"}},
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = _sanitize(info.get("title", "tiktok"))
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
            logger.info("yt-dlp failed for TikTok: %s", e)
            return None

    return await loop.run_in_executor(None, sync)


async def _try_ssstik(url: str) -> Optional[dict]:
    conn = aiohttp.TCPConnector(force_close=True)

    async with aiohttp.ClientSession(headers=HEADERS, connector=conn, timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            # ssstik.io requires a POST with the URL
            data = aiohttp.FormData()
            data.add_field("id", url)
            data.add_field("locale", "en")
            data.add_field("tt", "1")

            headers = {
                **HEADERS,
                "Origin": "https://ssstik.io",
                "Referer": "https://ssstik.io/",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            async with session.post(
                "https://ssstik.io/abc?url=dl",
                data=data,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    logger.warning("ssstik.io HTTP %d", resp.status)
                    return None
                html = await resp.text()
        except Exception as e:
            logger.warning("ssstik.io request failed: %s", e)
            return None

    # parse the result page for download link
    import re as regex

    video_url = None
    title = "tiktok_video"
    patterns = [
        r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*download[^"]*"[^>]*>',
        r'<a[^>]+class="[^"]*download[^"]*"[^>]+href="([^"]+)"[^>]*>',
        r'<a[^>]+href="([^"]+mp4[^"]+)"[^>]*>',
        r'"downloadUrl":"([^"]+)"',
    ]
    for pat in patterns:
        m = regex.search(pat, html)
        if m:
            video_url = m.group(1)
            break

    if not video_url:
        logger.warning("ssstik.io: no download link found")
        return None

    # sanitize URL
    video_url = video_url.replace("\\u0026", "&").replace("\\/", "/")
    if video_url.startswith("//"):
        video_url = "https:" + video_url

    # title
    title_match = regex.search(r"<title>(.*?)</title>", html)
    if title_match:
        t = title_match.group(1).strip()
        t = regex.sub(r"\s*\|\s*ssstik\S*", "", t, flags=regex.IGNORECASE).strip()
        if t:
            title = _sanitize(t)

    ext = "mp4"
    fname = f"{title}.{ext}"
    fpath = os.path.join(DOWNLOAD_DIR, fname)

    conn2 = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(headers=HEADERS, connector=conn2, timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.get(video_url) as resp:
                if resp.status != 200:
                    logger.warning("ssstik.io download HTTP %d", resp.status)
                    return None
                async with aiofiles.open(fpath, "wb") as f:
                    await f.write(await resp.read())
                return {
                    "filepath": fpath,
                    "filename": fname,
                    "filesize": os.path.getsize(fpath),
                    "file_type": "video",
                }
        except Exception as e:
            logger.warning("ssstik.io download failed: %s", e)
            return None
