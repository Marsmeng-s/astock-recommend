"""股票推荐工具配置"""
import os

# 涨幅筛选
MAX_CHANGE_PCT = 4.0
OPTIMAL_CHANGE_MIN = 1.0
OPTIMAL_CHANGE_MAX = 3.0
MIN_CHANGE_PCT = 0.0
MIN_STOCK_PRICE = 10.0   # 股价不低于 10 元

# 页面版本号（便于确认是否加载最新代码）
APP_VERSION = "20250606-1"

# 涨速初筛：只取前 20 只，不符合则刷新重筛
TOP_BY_SPEED = 20
MAX_REFRESH_ROUNDS = 4       # 最多刷新轮数（兼顾 14:45 前完成）
REFRESH_DELAY_SEC = 1        # 每轮间隔（秒）

# 趋势分析：只对涨幅筛选后的前 N 只做日线（加速）
TREND_ANALYSIS_MAX = 10
DAILY_HISTORY_WORKERS = 6    # 日线并行请求数

# 总耗时上限（秒），超时返回当前最优结果
MAX_SCREENING_SECONDS = 480  # 8 分钟

# 后续分析规模
TOP_BY_TREND = 10

# 日线分析
TREND_LOOKBACK_DAYS = 20
MIN_TREND_SCORE = 35

# 最终推荐数量
FINAL_RECOMMEND_COUNT = 2

# 严格模式：推荐股需超大单+大单均为正；多轮刷新仍无结果时自动降级
REQUIRE_POSITIVE_FLOW = True

# API 重试（东方财富直连，多节点轮换）
IS_CLOUD = os.environ.get("RENDER") == "true" or os.environ.get("MOBILE_ONLY") == "1"
API_RETRY_COUNT = 2 if IS_CLOUD else 5
API_RETRY_DELAY = 1 if IS_CLOUD else 2
API_TIMEOUT = 8 if IS_CLOUD else 20

if IS_CLOUD:
    MAX_REFRESH_ROUNDS = 2
    MAX_SCREENING_SECONDS = 300
    DAILY_HISTORY_WORKERS = 4

# Web 服务（0.0.0.0 = 允许任意网络访问，不限局域网）
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080

# 操作建议提示时段（仅 UI 展示，不限制检索时间）
TRADE_TIP = "下单操作建议在 14:45 - 15:00 之间进行"
SCREEN_TIP = "建议 14:35 左右点击检索，通常 3-8 分钟内完成"

LOG_DIR = "logs"
OUTPUT_DIR = "output"

# 账号登录
INITIAL_ACCOUNT_COUNT = 50
ACCOUNT_PREFIX = "user"
ACCOUNT_VALID_DAYS = 7          # 首次登录后有效天数
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@2025")  # 云端请在 Render 环境变量设置
SESSION_SECRET_FILE = "data/.session_secret"
