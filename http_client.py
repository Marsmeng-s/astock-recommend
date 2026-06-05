"""东方财富 API 请求（带重试、多节点、浏览器请求头）"""
from __future__ import annotations

import math
import random
import time
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

import config

EASTMONEY_HOSTS = [
    "https://82.push2.eastmoney.com",
    "https://90.push2.eastmoney.com",
    "https://91.push2.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
]

EASTMONEY_HIS_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://82.push2his.eastmoney.com",
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "close",
    "Referer": "https://quote.eastmoney.com/",
}


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def eastmoney_get(
    path: str,
    params: dict[str, Any],
    timeout: int | None = None,
    retries: int | None = None,
    hosts: list[str] | None = None,
) -> dict:
    """GET 东方财富 API，自动轮换节点并重试"""
    timeout = timeout or config.API_TIMEOUT
    retries = retries or config.API_RETRY_COUNT
    host_list = list(hosts or EASTMONEY_HOSTS)
    random.shuffle(host_list)
    last_err: Exception | None = None

    for attempt in range(retries):
        host = host_list[attempt % len(host_list)]
        url = f"{host}{path}"
        try:
            with _get_session() as session:
                resp = session.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("data"):
                    raise ValueError("接口返回空数据")
                return data
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(min(config.API_RETRY_DELAY * (attempt + 1), 8))

    raise ConnectionError(
        f"东方财富数据获取失败（已重试 {retries} 次）: {last_err}\n"
        "建议：1) 检查网络/代理  2) 在交易时段 14:45 左右重试  3) pip install akshare --upgrade"
    ) from last_err


def fetch_paginated_clist(
    path: str,
    base_params: dict[str, Any],
    *,
    max_pages: int | None = None,
    stop_when: int | None = None,
) -> pd.DataFrame:
    """
    分页拉取 clist 接口。
    max_pages: 最多拉取页数；stop_when: 已收集条数达到即停止。
    """
    data = eastmoney_get(path, base_params)
    diff = data["data"].get("diff") or []
    if not diff:
        return pd.DataFrame()

    frames = [pd.DataFrame(diff)]
    per_page = len(frames[0])
    total = int(data["data"].get("total") or per_page)
    total_page = max(1, math.ceil(total / per_page))
    if max_pages:
        total_page = min(total_page, max_pages)

    for page in range(2, total_page + 1):
        if stop_when and sum(len(f) for f in frames) >= stop_when:
            break
        params = {**base_params, "pn": str(page)}
        page_data = eastmoney_get(path, params)
        page_diff = page_data["data"].get("diff") or []
        if page_diff:
            frames.append(pd.DataFrame(page_diff))
        time.sleep(random.uniform(0.8, 1.5))

    return pd.concat(frames, ignore_index=True)
