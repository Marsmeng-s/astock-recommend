"""行情与资金数据获取"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import time

import pandas as pd

import config
from http_client import EASTMONEY_HIS_HOSTS, eastmoney_get, fetch_paginated_clist
from utils import retry_api

# 沪A + 深A 主板（排除创业板/科创板）
MAIN_BOARD_FS = "m:1 t:2,m:0 t:6"

SPOT_FIELDS = (
    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
    "f20,f21,f23,f24,f25,f22,f11,f62,f66,f72,f128,f136,f115,f152"
)


def _parse_spot_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """按东方财富字段名解析行情"""
    df = pd.DataFrame({
        "代码": raw["f12"].astype(str).str.zfill(6),
        "名称": raw["f14"],
        "最新价": pd.to_numeric(raw["f2"], errors="coerce"),
        "涨跌幅": pd.to_numeric(raw["f3"], errors="coerce"),
        "涨跌额": pd.to_numeric(raw.get("f4"), errors="coerce"),
        "成交量": pd.to_numeric(raw.get("f5"), errors="coerce"),
        "成交额": pd.to_numeric(raw.get("f6"), errors="coerce"),
        "振幅": pd.to_numeric(raw.get("f7"), errors="coerce"),
        "最高": pd.to_numeric(raw.get("f15"), errors="coerce"),
        "最低": pd.to_numeric(raw.get("f16"), errors="coerce"),
        "今开": pd.to_numeric(raw.get("f17"), errors="coerce"),
        "昨收": pd.to_numeric(raw.get("f18"), errors="coerce"),
        "量比": pd.to_numeric(raw.get("f10"), errors="coerce"),
        "换手率": pd.to_numeric(raw.get("f8"), errors="coerce"),
        "涨速": pd.to_numeric(raw.get("f22"), errors="coerce"),
        "5分钟涨跌": pd.to_numeric(raw.get("f11"), errors="coerce"),
        "超大单净流入": pd.to_numeric(raw.get("f66"), errors="coerce"),
        "大单净流入": pd.to_numeric(raw.get("f72"), errors="coerce"),
        "60日涨跌幅": pd.to_numeric(raw.get("f24"), errors="coerce"),
        "年初至今涨跌幅": pd.to_numeric(raw.get("f25"), errors="coerce"),
    })
    return df.dropna(subset=["代码", "涨速"])


def fetch_spot_quotes() -> pd.DataFrame:
    """
    获取主板 A 股实时行情（按涨速降序，单页 Top100 足够初筛 Top20）
    """
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f22",          # 按涨速排序
        "fs": MAIN_BOARD_FS,
        "fields": SPOT_FIELDS,
    }
    data = eastmoney_get("/api/qt/clist/get", params)
    diff = data["data"].get("diff") or []
    if not diff:
        raise ConnectionError("未获取到行情数据")
    return _parse_spot_raw(pd.DataFrame(diff))


def _parse_fund_flow_raw(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({
        "代码": raw["f12"].astype(str).str.zfill(6),
        "名称": raw["f14"],
        "最新价": pd.to_numeric(raw.get("f2"), errors="coerce"),
        "今日涨跌幅": pd.to_numeric(raw.get("f3"), errors="coerce"),
        "今日超大单净流入-净额": pd.to_numeric(raw.get("f66"), errors="coerce"),
        "今日大单净流入-净额": pd.to_numeric(raw.get("f72"), errors="coerce"),
    })
    return df


def fetch_fund_flow_for_codes(codes: set[str]) -> pd.DataFrame:
    """分页拉取资金流，找齐候选股后立即停止"""
    if not codes:
        return pd.DataFrame()

    params = {
        "fid": "f62",
        "po": "1",
        "pz": "100",
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
        "fields": "f12,f14,f2,f3,f62,f66,f72",
    }

    found: list[pd.DataFrame] = []
    remaining = set(codes)

    for page in range(1, 16):
        if not remaining:
            break
        data = eastmoney_get("/api/qt/clist/get", {**params, "pn": str(page)}, retries=3)
        diff = data["data"].get("diff") or []
        if not diff:
            break
        page_df = _parse_fund_flow_raw(pd.DataFrame(diff))
        hit = page_df[page_df["代码"].isin(remaining)]
        if not hit.empty:
            found.append(hit)
            remaining -= set(hit["代码"])
        if remaining and page < 15:
            time.sleep(0.25)

    if not found:
        return pd.DataFrame()
    return pd.concat(found, ignore_index=True).drop_duplicates(subset=["代码"])


def fetch_fund_flow_rank() -> pd.DataFrame:
    """兼容旧接口：拉取今日资金流排名（分页）"""
    params = {
        "fid": "f62",
        "po": "1",
        "pz": "100",
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124",
    }
    raw = fetch_paginated_clist("/api/qt/clist/get", params, max_pages=20)
    if raw.empty:
        raise ConnectionError("未获取到资金流数据")
    return _parse_fund_flow_raw(raw)


def fetch_daily_history(code: str, days: int = 30) -> pd.DataFrame:
    """获取个股近期日线（前复权）"""
    market_code = 1 if code.startswith("6") else 0
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=days * 2)).strftime("%Y%m%d")
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "101",
        "fqt": "1",
        "secid": f"{market_code}.{code}",
        "beg": start,
        "end": end,
    }
    data = eastmoney_get(
        "/api/qt/stock/kline/get",
        params,
        hosts=EASTMONEY_HIS_HOSTS,
        retries=2,
        timeout=10,
    )
    klines = (data.get("data") or {}).get("klines") or []
    if not klines:
        return pd.DataFrame()

    rows = [line.split(",") for line in klines]
    df = pd.DataFrame(rows, columns=[
        "日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额",
        "振幅", "涨跌幅", "涨跌额", "换手率", "_",
    ][: len(rows[0])])
    for col in ("开盘", "收盘", "最高", "最低"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.tail(days).reset_index(drop=True)


def fetch_daily_histories_parallel(
    codes: list[str],
    cache: dict[str, pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    """并行拉取多只股票的日线，同一会话内复用缓存"""
    result: dict[str, pd.DataFrame] = {}
    pending: list[str] = []
    for code in codes:
        if cache is not None and code in cache:
            result[code] = cache[code]
        else:
            pending.append(code)

    if not pending:
        return result

    workers = min(config.DAILY_HISTORY_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_daily_history, code): code for code in pending}
        for future in as_completed(futures):
            code = futures[future]
            try:
                hist = future.result()
            except Exception:
                hist = pd.DataFrame()
            result[code] = hist
            if cache is not None:
                cache[code] = hist
    return result


def fetch_industry_flow() -> pd.DataFrame:
    """获取行业板块今日资金流向（东方财富）"""
    params = {
        "fid": "f62",
        "po": "1",
        "pz": "10",
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fs": "m:90 t:2",
        "fields": "f14,f62",
    }
    data = eastmoney_get("/api/qt/clist/get", params, retries=3)
    diff = data["data"].get("diff") or []
    if not diff:
        return pd.DataFrame()
    df = pd.DataFrame(diff)
    return df.rename(columns={"f14": "行业", "f62": "净额"})
