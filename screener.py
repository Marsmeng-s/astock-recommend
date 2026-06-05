"""五步筛选与综合评分逻辑"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import pandas as pd

import config
from data_fetcher import (
    fetch_daily_histories_parallel,
    fetch_fund_flow_for_codes,
    fetch_spot_quotes,
)
from utils import format_money, is_main_board, is_st_stock, now_str

ProgressCallback = Callable[[str, dict], None]


@dataclass
class StockCandidate:
    code: str
    name: str
    price: float
    speed: float
    change_pct: float
    change_score: float = 0.0
    trend_score: float = 0.0
    trend_detail: str = ""
    super_large_flow: float = 0.0
    large_flow: float = 0.0
    flow_from_spot: bool = False
    flow_score: float = 0.0
    total_score: float = 0.0
    rank_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["super_large_flow_text"] = format_money(self.super_large_flow)
        d["large_flow_text"] = format_money(self.large_flow)
        return d


def _change_pct_score(pct: float) -> float:
    if config.OPTIMAL_CHANGE_MIN <= pct <= config.OPTIMAL_CHANGE_MAX:
        return 100.0
    if 0 < pct < config.OPTIMAL_CHANGE_MIN:
        return 60.0 + (pct / config.OPTIMAL_CHANGE_MIN) * 30.0
    if config.OPTIMAL_CHANGE_MAX < pct <= config.MAX_CHANGE_PCT:
        span = config.MAX_CHANGE_PCT - config.OPTIMAL_CHANGE_MAX
        return 90.0 - ((pct - config.OPTIMAL_CHANGE_MAX) / span) * 30.0
    return 0.0


def analyze_daily_trend(hist: pd.DataFrame) -> tuple[float, str]:
    if hist is None or len(hist) < 10:
        return 0.0, "日线数据不足"

    recent = hist.tail(config.TREND_LOOKBACK_DAYS).copy()
    closes = recent["收盘"].astype(float).values
    lows = recent["最低"].astype(float).values
    highs = recent["最高"].astype(float).values

    current = closes[-1]
    min_price = lows.min()
    max_price = highs.max()
    price_range = max_price - min_price

    if price_range <= 0:
        return 30.0, "价格波动极小"

    position = (current - min_price) / price_range

    consecutive_up = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            consecutive_up += 1
        else:
            break

    last_10 = recent.tail(10)
    low_idx = last_10["最低"].astype(float).idxmin()
    days_since_low = len(recent) - 1 - low_idx
    recent_low = float(last_10.loc[low_idx, "最低"])
    rise_from_low = (current - recent_low) / recent_low * 100 if recent_low > 0 else 0

    score = 70.0
    notes: list[str] = []

    if position >= 0.92:
        score -= 45
        notes.append("接近20日最高点")
    elif position >= 0.85:
        score -= 25
        notes.append("处于近期高位区")
    elif position >= 0.75:
        score -= 10
        notes.append("偏高位")

    if consecutive_up >= 5:
        score -= 35
        notes.append(f"连续{consecutive_up}日上涨")
    elif consecutive_up >= 3:
        score -= 20
        notes.append(f"连续{consecutive_up}日上涨")

    if 0.20 <= position <= 0.70:
        score += 20
        notes.append("中低位上行")
    if 2 <= days_since_low <= 12 and 1.0 <= rise_from_low <= 8.0:
        score += 25
        notes.append(f"近{days_since_low}日前低点上行{rise_from_low:.1f}%")
    elif days_since_low <= 2 and rise_from_low <= 3.0:
        score += 10
        notes.append("刚脱离近期低点")

    score = max(0.0, min(100.0, score))
    detail = "；".join(notes) if notes else "趋势正常"
    return score, detail


def _normalize_series(values: pd.Series) -> pd.Series:
    vmin, vmax = values.min(), values.max()
    if vmax <= vmin:
        return pd.Series([50.0] * len(values), index=values.index)
    return (values - vmin) / (vmax - vmin) * 100.0


def step1_filter_board(spot: pd.DataFrame) -> pd.DataFrame:
    df = spot.copy()
    df = df[df["代码"].apply(is_main_board)]
    df = df[~df["名称"].apply(is_st_stock)]
    df = df[df["涨速"].notna() & df["涨跌幅"].notna()]
    df = df[df["最新价"].notna() & (df["最新价"] >= config.MIN_STOCK_PRICE)]
    return df


def step2_top_by_speed(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("涨速", ascending=False).head(config.TOP_BY_SPEED)


def step3_filter_change(df: pd.DataFrame) -> pd.DataFrame:
    filtered = df[
        (df["涨跌幅"] > config.MIN_CHANGE_PCT)
        & (df["涨跌幅"] <= config.MAX_CHANGE_PCT)
    ].copy()
    if filtered.empty:
        return filtered
    filtered["change_score"] = filtered["涨跌幅"].apply(_change_pct_score)
    speed_norm = _normalize_series(filtered["涨速"])
    filtered["pre_score"] = speed_norm * 0.55 + filtered["change_score"] * 0.45
    return filtered.sort_values("pre_score", ascending=False)


def _spot_flow_from_row(row: pd.Series) -> tuple[float | None, float | None]:
    if "超大单净流入" not in row.index or "大单净流入" not in row.index:
        return None, None
    super_flow = row["超大单净流入"]
    large_flow = row["大单净流入"]
    if pd.isna(super_flow) or pd.isna(large_flow):
        return None, None
    return float(super_flow), float(large_flow)


def step4_analyze_trend(
    candidates: pd.DataFrame,
    progress: ProgressCallback | None = None,
    history_cache: dict | None = None,
) -> list[StockCandidate]:
    subset = candidates.head(config.TREND_ANALYSIS_MAX)
    codes = subset["代码"].tolist()
    total = len(subset)

    if progress:
        progress(f"日线并行分析 {total} 只...", {"step": 4, "total": total})

    histories = fetch_daily_histories_parallel(codes, cache=history_cache)
    results: list[StockCandidate] = []

    for idx, (_, row) in enumerate(subset.iterrows(), 1):
        code = row["代码"]
        hist = histories.get(code, pd.DataFrame())
        try:
            trend_score, detail = analyze_daily_trend(hist)
        except Exception as e:
            trend_score, detail = 0.0, f"分析失败: {e}"

        if trend_score < config.MIN_TREND_SCORE:
            continue

        super_flow, large_flow = _spot_flow_from_row(row)
        has_spot_flow = super_flow is not None and large_flow is not None
        results.append(
            StockCandidate(
                code=code,
                name=row["名称"],
                price=float(row["最新价"]),
                speed=float(row["涨速"]),
                change_pct=float(row["涨跌幅"]),
                change_score=float(row["change_score"]),
                trend_score=trend_score,
                trend_detail=detail,
                super_large_flow=super_flow if has_spot_flow else 0.0,
                large_flow=large_flow if has_spot_flow else 0.0,
                flow_from_spot=has_spot_flow,
            )
        )

    results.sort(
        key=lambda x: x.speed * 0.3 + x.change_score * 0.3 + x.trend_score * 0.4,
        reverse=True,
    )
    return results[: config.TOP_BY_TREND]


def step5_fund_flow(
    candidates: list[StockCandidate],
) -> tuple[list[StockCandidate], str | None]:
    if not candidates:
        return [], "无候选股"

    need_fetch = [c for c in candidates if not c.flow_from_spot]
    if need_fetch:
        try:
            codes = {c.code for c in need_fetch}
            flow_df = fetch_fund_flow_for_codes(codes)
            if flow_df.empty:
                raise ValueError("候选股无资金流数据")
            flow_map = flow_df.set_index("代码")
            super_col = "今日超大单净流入-净额"
            large_col = "今日大单净流入-净额"
            for c in need_fetch:
                if c.code not in flow_map.index:
                    c.rank_notes.append("无资金流数据")
                    continue
                row = flow_map.loc[c.code]
                c.super_large_flow = float(row.get(super_col, 0) or 0)
                c.large_flow = float(row.get(large_col, 0) or 0)
                c.flow_from_spot = True
        except Exception as e:
            for c in candidates:
                c.total_score = c.speed * 0.35 + c.change_score * 0.30 + c.trend_score * 0.35
            candidates.sort(key=lambda x: x.total_score, reverse=True)
            return candidates, f"资金流获取失败({e})，已按涨速/涨幅/趋势排序"

    for c in candidates:
        if c.super_large_flow <= 0 or c.large_flow <= 0:
            c.flow_score = 0.0
            c.rank_notes.append("超大单或大单非正")
            continue
        c.flow_score = c.super_large_flow * 0.6 + c.large_flow * 0.4

    positive = [c for c in candidates if c.flow_score > 0]
    if not positive:
        for c in candidates:
            c.total_score = c.speed * 0.35 + c.change_score * 0.30 + c.trend_score * 0.35
        candidates.sort(key=lambda x: x.total_score, reverse=True)
        return candidates, "无超大单+大单均为正，已降级排序"

    max_flow = max(c.flow_score for c in positive)
    for c in positive:
        flow_norm = (c.flow_score / max_flow) * 100 if max_flow > 0 else 0
        speed_norm = min(c.speed / max(x.speed for x in positive), 1) * 100
        c.total_score = (
            speed_norm * 0.25
            + c.change_score * 0.20
            + c.trend_score * 0.30
            + flow_norm * 0.25
        )

    positive.sort(key=lambda x: x.total_score, reverse=True)
    return positive, None


def _filter_min_price(candidates: list[StockCandidate]) -> list[StockCandidate]:
    return [c for c in candidates if c.price >= config.MIN_STOCK_PRICE]


def _pick_final(final: list[StockCandidate], pool: list[StockCandidate]) -> list[StockCandidate]:
    min_price = config.MIN_STOCK_PRICE
    final = [c for c in final if c.price >= min_price]
    pool = [c for c in pool if c.price >= min_price]
    positive = [c for c in final if c.flow_score > 0]
    picks = positive[: config.FINAL_RECOMMEND_COUNT]

    if len(picks) < config.FINAL_RECOMMEND_COUNT:
        existing = {c.code for c in picks}
        for c in final:
            if c.code not in existing:
                if c.total_score == 0:
                    c.total_score = c.speed * 0.3 + c.change_score * 0.3 + c.trend_score * 0.4
                picks.append(c)
                existing.add(c.code)
            if len(picks) >= config.FINAL_RECOMMEND_COUNT:
                break

    if len(picks) < config.FINAL_RECOMMEND_COUNT:
        existing = {c.code for c in picks}
        for c in pool:
            if c.code not in existing:
                picks.append(c)
            if len(picks) >= config.FINAL_RECOMMEND_COUNT:
                break

    return picks[: config.FINAL_RECOMMEND_COUNT]


def _round_meets_target(picks: list[StockCandidate]) -> bool:
    picks = _filter_min_price(picks)
    if len(picks) < config.FINAL_RECOMMEND_COUNT:
        return False
    if not config.REQUIRE_POSITIVE_FLOW:
        return True
    return all(c.flow_score > 0 for c in picks[: config.FINAL_RECOMMEND_COUNT])


def run_one_round(
    progress: ProgressCallback | None = None,
    round_num: int = 1,
    history_cache: dict | None = None,
) -> tuple[list[StockCandidate], list[str]]:
    """单轮筛选：涨速 Top20 → 五步筛查 → 最多 2 只推荐"""
    log: list[str] = []

    def _prog(msg: str, extra: dict | None = None):
        if progress:
            progress(msg, {"round": round_num, **(extra or {})})

    _prog("获取实时行情...")
    log.append(f"[第{round_num}轮] 获取行情...")
    spot = fetch_spot_quotes()
    board = step1_filter_board(spot)
    log.append(f"  主板 {len(board)} 只（股价≥{config.MIN_STOCK_PRICE}元）")

    _prog("涨速 Top20 初筛...")
    by_speed = step2_top_by_speed(board)
    if by_speed.empty:
        log.append("  涨速榜为空")
        return [], log
    log.append(f"  涨速前20，最高 {by_speed['涨速'].iloc[0]:.2f}%")

    _prog("涨幅筛选...")
    by_change = step3_filter_change(by_speed)
    log.append(f"  涨幅合格 {len(by_change)} 只")
    if by_change.empty:
        return [], log

    _prog("日线趋势分析...")
    by_trend = step4_analyze_trend(by_change, progress, history_cache)
    log.append(f"  趋势合格 {len(by_trend)} 只（分析前{config.TREND_ANALYSIS_MAX}）")

    if not by_trend:
        log.append("  趋势全淘汰，降级取涨速/涨幅优者")
        for _, row in by_change.head(config.TREND_ANALYSIS_MAX).iterrows():
            super_flow, large_flow = _spot_flow_from_row(row)
            has_spot_flow = super_flow is not None and large_flow is not None
            by_trend.append(
                StockCandidate(
                    code=row["代码"],
                    name=row["名称"],
                    price=float(row["最新价"]),
                    speed=float(row["涨速"]),
                    change_pct=float(row["涨跌幅"]),
                    change_score=float(row["change_score"]),
                    trend_score=0.0,
                    trend_detail="降级：未过趋势筛查",
                    super_large_flow=super_flow if has_spot_flow else 0.0,
                    large_flow=large_flow if has_spot_flow else 0.0,
                    flow_from_spot=has_spot_flow,
                )
            )

    _prog("资金流向分析...")
    final, flow_warn = step5_fund_flow(by_trend)
    if flow_warn:
        log.append(f"  {flow_warn}")

    picks = _filter_min_price(_pick_final(final, by_trend))
    log.append(f"  本轮产出 {len(picks)} 只")
    return picks, log


def run_screening(
    progress: ProgressCallback | None = None,
) -> tuple[list[StockCandidate], str]:
    """
    带自动刷新的完整筛选：
    涨速 Top20 不符合 → 刷新重筛，直到得到最优 2 只或达最大轮数
    """
    all_logs: list[str] = [f"=== 股票推荐 {now_str()} ==="]
    best_picks: list[StockCandidate] = []
    best_score = -1.0
    started = time.perf_counter()
    history_cache: dict = {}

    for round_num in range(1, config.MAX_REFRESH_ROUNDS + 1):
        elapsed = time.perf_counter() - started
        if elapsed >= config.MAX_SCREENING_SECONDS:
            all_logs.append(f"\n已达耗时上限 {config.MAX_SCREENING_SECONDS // 60} 分钟，返回当前最优")
            break

        if progress:
            progress(f"第 {round_num}/{config.MAX_REFRESH_ROUNDS} 轮检索...", {"round": round_num})

        try:
            picks, round_log = run_one_round(progress, round_num, history_cache)
        except Exception as e:
            all_logs.append(f"[第{round_num}轮] 失败: {e}")
            if round_num < config.MAX_REFRESH_ROUNDS:
                time.sleep(config.REFRESH_DELAY_SEC)
            continue

        all_logs.extend(round_log)

        if picks:
            score = sum(c.total_score or c.speed for c in picks)
            if score > best_score:
                best_score = score
                best_picks = picks

        if _round_meets_target(picks):
            all_logs.append(f"\n第 {round_num} 轮找到符合条件的 {len(picks)} 只股票")
            return _filter_min_price(picks)[: config.FINAL_RECOMMEND_COUNT], _format_report(all_logs, picks, round_num)

        if round_num < config.MAX_REFRESH_ROUNDS:
            all_logs.append(f"  本轮未达最优，{config.REFRESH_DELAY_SEC}s 后刷新...")
            if progress:
                progress("不符合条件，刷新重筛...", {"round": round_num, "refreshing": True})
            time.sleep(config.REFRESH_DELAY_SEC)

    picks = _filter_min_price(best_picks)[: config.FINAL_RECOMMEND_COUNT]
    all_logs.append(f"\n已达最大刷新 {config.MAX_REFRESH_ROUNDS} 轮，返回当前最优结果")
    return picks, _format_report(all_logs, picks, config.MAX_REFRESH_ROUNDS)


def _format_report(logs: list[str], picks: list[StockCandidate], rounds: int) -> str:
    lines = logs.copy()
    lines.append(f"\n=== 最终推荐（共 {rounds} 轮）{len(picks)} 只 ===")
    for i, c in enumerate(picks, 1):
        lines.append(
            f"\n#{i} {c.name}({c.code})\n"
            f"  价:{c.price:.2f} 涨速:{c.speed:.2f}% 涨幅:{c.change_pct:.2f}%\n"
            f"  趋势:{c.trend_detail} ({c.trend_score:.0f}分)\n"
            f"  超大单:{format_money(c.super_large_flow)} 大单:{format_money(c.large_flow)}\n"
            f"  综合:{c.total_score:.1f}"
        )
    return "\n".join(lines)
