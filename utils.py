"""通用工具函数"""
from __future__ import annotations

import time
from datetime import date, datetime
from functools import wraps
from typing import Callable, TypeVar

import chinese_calendar as cc
import pandas as pd

T = TypeVar("T")


def is_trading_day(d: date | None = None) -> bool:
    """判断是否为 A 股交易日（排除周末和法定节假日）"""
    d = d or date.today()
    if d.weekday() >= 5:
        return False
    return cc.is_workday(d)


def is_main_board(code: str) -> bool:
    """
    判断是否为上/深 A 股主板（排除创业板 300、科创板 688、北交所等）
    """
    code = str(code).zfill(6)
    if code.startswith("300"):
        return False  # 创业板
    if code.startswith("688"):
        return False  # 科创板
    if code.startswith(("8", "4")):
        return False  # 北交所
    # 上证主板 60x / 601 / 603 / 605
    if code.startswith(("600", "601", "603", "605")):
        return True
    # 深证主板 000 / 001 / 002 / 003
    if code.startswith(("000", "001", "002", "003")):
        return True
    return False


def get_market(code: str) -> str:
    """返回 akshare 所需的市场标识 sh / sz"""
    code = str(code).zfill(6)
    if code.startswith(("6", "5")):
        return "sh"
    return "sz"


def is_st_stock(name: str) -> bool:
    """排除 ST / 退市股"""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return True
    text = str(name).strip()
    if not text:
        return True
    upper = text.upper()
    return "ST" in upper or "退" in text


def retry_api(retries: int = 3, delay: float = 5.0) -> Callable:
    """API 调用重试装饰器"""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_err: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt < retries:
                        time.sleep(delay * attempt)
            raise last_err  # type: ignore[misc]

        return wrapper

    return decorator


def format_money(value: float) -> str:
    """格式化资金金额（元 -> 万/亿）"""
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{value:.0f}元"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
