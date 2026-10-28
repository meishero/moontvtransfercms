#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
moontvtransfercms — 将 MoonTV 订阅转换为 LibreTV-SourceList（CMS）JSON

数据流：
  订阅地址（base58 编码的 MoonTV 配置 JSON）
    -> base58/base64 解码
    -> {"cache_time":..., "api_site": {key: {name, api, detail}}}
    -> LibreTV-SourceList 格式 cms.json

输出格式（LibreTV 数据源订阅，官方文档）：
  {
    "name": "...",
    "version": 2,
    "sources": [{"name": ..., "url": ..., "detail": ..., "isAdult": false}],
    "liveSources": []
  }

约束（与 LibreTV 导入端保持一致）：
  - sources 最多 100 个，超出截断
  - 按 url 去重（先到先得）
  - 仅保留公网 http/https 地址
  - url 去掉尾部斜杠

用法：
  python convert.py                 # 使用默认订阅地址，输出 cms.json
  SUB_URL="https://..." python convert.py
  OUTPUT_FILE=out.json python convert.py
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 配置（可用环境变量覆盖）

DEFAULT_SUB_URL = "https://pz.v88.qzz.io?format=2&source=jin18"
SUB_URL = os.environ.get("SUB_URL", DEFAULT_SUB_URL)
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "cms.json")
LIST_NAME = os.environ.get("LIST_NAME", "MoonTV 聚合点播源")

FETCH_TIMEOUT = 30      # 单次请求超时（秒）
FETCH_RETRIES = 3       # 拉取失败重试次数
MAX_SOURCES = 100       # LibreTV 点播源上限

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_INDEX = {c: i for i, c in enumerate(B58_ALPHABET)}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------- base58 解码

def b58decode(text: str) -> bytes:
    """Bitcoin 字母表 base58 解码（不依赖第三方库，GitHub Actions 直接可用）。"""
    if not text:
        raise ValueError("empty base58 input")
    # 前导 '1' 对应前导零字节
    pad = 0
    for ch in text:
        if ch == "1":
            pad += 1
        else:
            break
    num = 0
    for ch in text:
        if ch not in B58_INDEX:
            raise ValueError("invalid base58 char: %r" % ch)
        num = num * 58 + B58_INDEX[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * pad + body


# ---------------------------------------------------------------- 拉取与解码

def fetch_subscription(url: str) -> str:
    """带重试的订阅拉取。"""
    last_err = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="strict").strip()
        except Exception as e:  # noqa: BLE001 - 需要把任何网络错误转成可读信息
            last_err = e
            print("[warn] 第 %d/%d 次拉取失败: %s" % (attempt, FETCH_RETRIES, e), file=sys.stderr)
            if attempt < FETCH_RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError("拉取订阅失败（重试 %d 次后仍失败）: %s" % (FETCH_RETRIES, last_err))


def decode_subscription(text: str) -> dict:
    """
    自适应解码：
      1) 本身是 JSON            -> 直接用
      2) base58 编码的 JSON      -> 解码（本订阅的格式）
      3) base64 编码的 JSON      -> 解码（兜底，换源时可能遇到）
    """
    candidates = [text]
    try:
        candidates.append(b58decode(text).decode("utf-8"))
    except Exception:
        pass
    try:
        padded = text + "=" * (-len(text) % 4)
        candidates.append(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        pass

    for cand in candidates:
        cand = cand.strip()
        if not cand.startswith("{") and not cand.startswith("["):
            continue
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            # 裸数组按接口目录处理
            return {"__bare_list__": data}
    raise ValueError("无法解码订阅内容（已尝试 JSON / base58 / base64）")


# ---------------------------------------------------------------- 格式转换

def _clean_url(url) -> str:
    """校验并归一化 URL：仅公网 http(s)，去掉尾部斜杠。"""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    lower = url.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return ""
    return url.rstrip("/")


def _hostname(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or url
    except Exception:
        return url


def _name_of(entry: dict, fallback_key: str, url: str) -> str:
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return fallback_key or _hostname(url)


def convert_to_sourcelist(data: dict, list_name: str) -> dict:
    """
    MoonTV 配置 -> LibreTV-SourceList。

    支持两种输入结构：
      A. {"api_site": {key: {name, api, detail}}}   MoonTV / LibreTV 经典配置
      B. {"sites": [{name, api|url, detail}]}        TVBOX 配置（换源兜底）
      C. {"__bare_list__": [{name, baseUrl}]}        接口目录
    """
    raw_entries = []  # [(fallback_key, name, api, detail)]

    if isinstance(data.get("api_site"), dict):
        for key, site in data["api_site"].items():
            if not isinstance(site, dict):
                continue
            raw_entries.append(
                (key, site.get("name"), site.get("api") or site.get("url"), site.get("detail"))
            )
    elif isinstance(data.get("sites"), list):
        for site in data["sites"]:
            if not isinstance(site, dict):
                continue
            raw_entries.append(
                (
                    site.get("key") or "",
                    site.get("name"),
                    site.get("api") or site.get("url"),
                    site.get("detail"),
                )
            )
    elif isinstance(data.get("__bare_list__"), list):
        for site in data["__bare_list__"]:
            if not isinstance(site, dict):
                continue
            raw_entries.append(("", site.get("name"), site.get("baseUrl") or site.get("url"), None))
    else:
        raise ValueError("订阅 JSON 中未找到 api_site / sites / 接口目录字段")

    sources = []
    seen = set()
    skipped = 0
    for key, name, api, detail in raw_entries:
        url = _clean_url(api)
        if not url:
            skipped += 1
            continue
        if url in seen:  # LibreTV 按 url 去重，先到先得
            skipped += 1
            continue
        seen.add(url)
        item = {
            "name": _name_of({"name": name}, key, url),
            "url": url,
            "isAdult": False,
        }
        detail_url = _clean_url(detail)
        if detail_url:
            item["detail"] = detail_url
        sources.append(item)

    if not sources:
        raise ValueError("转换结果为空：没有有效的点播源（url 全部缺失或非法）")

    if len(sources) > MAX_SOURCES:  # LibreTV 上限 100，超出截断
        print("[warn] 源数量 %d 超过上限 %d，已截断" % (len(sources), MAX_SOURCES))
        sources = sources[:MAX_SOURCES]

    return {
        "name": list_name,
        "version": 2,
        "sources": sources,
        "liveSources": [],
    }


# ---------------------------------------------------------------- 主流程

def main() -> int:
    print("[info] 订阅地址: %s" % SUB_URL)
    raw_text = fetch_subscription(SUB_URL)
    print("[info] 订阅内容长度: %d 字节" % len(raw_text))

    data = decode_subscription(raw_text)
    if "api_site" in data:
        total = len(data["api_site"])
    elif "sites" in data:
        total = len(data["sites"])
    else:
        total = len(data.get("__bare_list__", []))
    print("[info] 解析到原始站点数: %d" % total)

    result = convert_to_sourcelist(data, LIST_NAME)
    kept = len(result["sources"])
    print("[info] 转换后有效点播源: %d 个（跳过 %d 个无效/重复）" % (kept, total - kept if total >= kept else 0))

    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"

    # 原子写入：先写临时文件再替换，避免中断时损坏 cms.json
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, OUTPUT_FILE)

    print("[ok] 已写入 %s（%d 字节）" % (OUTPUT_FILE, len(payload.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - 顶层兜底：失败时退出码非 0，让 Action 报错而不是提交坏数据
        print("[error] %s" % e, file=sys.stderr)
        sys.exit(1)
