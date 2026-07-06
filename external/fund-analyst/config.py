"""
config.py —— 公共配置与工具函数
===============================================
所有脚本共用的配置、常量、工具函数。
"""

import os
import json
import time
import logging
from datetime import datetime, timedelta
from functools import wraps

# ============ 基础配置 ============

# 数据缓存目录（避免频繁请求被限流）
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 缓存有效期（秒）
CACHE_EXPIRE = {
    "realtime": 60 * 5,           # 实时数据 5 分钟
    "daily": 60 * 60 * 4,         # 日级数据 4 小时
    "quarterly": 60 * 60 * 24,    # 季度数据 1 天
    "static": 60 * 60 * 24 * 7,   # 静态数据 7 天
}

# 请求重试配置
MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_logger(name):
    """获取标准logger"""
    return logging.getLogger(name)


# ============ 常量配置 ============

BASELINE_VALIDATION_FUND = {
    "code": "011892",
    "name": "易方达先锋成长混合C",
    "purpose": "后续 skill 升级与预测逻辑的固定基准验证样本",
}

# 主要行业/板块代码映射（东方财富板块代码）
SECTOR_CODES = {
    "人工智能":    "BK1086",
    "半导体":      "BK1036",
    "新能源":      "BK0493",
    "光伏":        "BK0478",
    "储能":        "BK1059",
    "机器人":      "BK0617",
    "消费电子":    "BK0448",
    "白酒":        "BK0759",
    "医药":        "BK0727",
    "创新药":      "BK1156",
    "银行":        "BK0475",
    "证券":        "BK0473",
    "有色金属":    "BK0478",
    "电解铝":      "BK0479",
    "煤炭":        "BK0437",
    "电力":        "BK0428",
    "红利":        "BK0420",
    "军工":        "BK0490",
    "房地产":      "BK0451",
}

# 全球指数代码
GLOBAL_INDICES = {
    "纳斯达克100": "NDX",
    "标普500":    "SPX",
    "道琼斯":     "DJI",
    "恒生指数":   "HSI",
    "恒生科技":   "HSTECH",
    "VIX":        "^VIX",
}

# 重大节假日列表（可在脚本中动态补充）
MAJOR_HOLIDAYS = {
    "春节":       {"duration": 7, "risk_level": "🔴 极高"},
    "国庆节":     {"duration": 7, "risk_level": "🔴 极高"},
    "劳动节":     {"duration": 5, "risk_level": "🟠 高"},
    "中秋节":     {"duration": 3, "risk_level": "🟡 中"},
    "元旦":       {"duration": 3, "risk_level": "🟡 中"},
    "清明节":     {"duration": 3, "risk_level": "🟢 低"},
    "端午节":     {"duration": 3, "risk_level": "🟢 低"},
}

# v6.0 统一风控参数：所有买入、持仓跟踪、解套脚本必须复用这里的阈值。
RISK_CONTROL_V6 = {
    "version": "v6.0",
    "normal": {
        "label": "🟢 向上/风险可控",
        "allow_new_buy": True,
        "position_multiplier": 1.0,
        "initial_stop_pct": -5.0,
        "hard_stop_pct": -8.0,
        "trailing_buffer_pct": 5.0,
    },
    "neutral": {
        "label": "🟡 震荡/半开",
        "allow_new_buy": True,
        "position_multiplier": 0.5,
        "initial_stop_pct": -4.0,
        "hard_stop_pct": -6.0,
        "trailing_buffer_pct": 4.0,
    },
    "downtrend": {
        "label": "🔴 下跌/关闭",
        "allow_new_buy": False,
        "position_multiplier": 0.0,
        "initial_stop_pct": -3.0,
        "hard_stop_pct": -5.0,
        "trailing_buffer_pct": 3.0,
    },
}

MARKET_SWITCH_THRESHOLDS_V6 = {
    "open_min_score": 75,
    "half_open_min_score": 50,
    "score_weights": {
        "broad_market": 0.4,
        "sector": 0.4,
        "global_linkage": 0.2,
    },
}


def normalize_market_state(market_state="normal"):
    """把中文/英文市场状态统一到 normal/neutral/downtrend。"""
    if market_state is None:
        return "normal"
    text = str(market_state).lower()
    if any(k in text for k in ["down", "bear", "关闭", "向下", "下跌", "空头", "🔴"]):
        return "downtrend"
    if any(k in text for k in ["neutral", "half", "震荡", "半开", "观望", "🟡"]):
        return "neutral"
    return "normal"


def classify_market_switch_score(score):
    """根据三层方向加权得分输出 v6.0 大盘总开关状态。"""
    if score is None:
        return {
            "state": "neutral",
            "label": RISK_CONTROL_V6["neutral"]["label"],
            "switch": "🟡 半开",
            "reason": "总开关得分缺失，默认按震荡处理",
        }
    if score >= MARKET_SWITCH_THRESHOLDS_V6["open_min_score"]:
        state = "normal"
        switch = "🟢 打开"
    elif score >= MARKET_SWITCH_THRESHOLDS_V6["half_open_min_score"]:
        state = "neutral"
        switch = "🟡 半开"
    else:
        state = "downtrend"
        switch = "🔴 关闭"
    return {
        "state": state,
        "label": RISK_CONTROL_V6[state]["label"],
        "switch": switch,
        "reason": f"三层方向加权得分 {score}/100",
    }


def get_risk_profile(market_state="normal"):
    """返回指定市场状态下的止损、仓位和移动止损参数。"""
    state = normalize_market_state(market_state)
    profile = RISK_CONTROL_V6[state].copy()
    profile["state"] = state
    return profile


def calc_stop_prices(price, market_state="normal"):
    """根据 v6.0 风控参数计算初始止损与硬止损价。"""
    profile = get_risk_profile(market_state)
    return {
        "market_state": profile["state"],
        "profile_label": profile["label"],
        "initial_stop_pct": profile["initial_stop_pct"],
        "hard_stop_pct": profile["hard_stop_pct"],
        "initial_stop_price": round(price * (1 + profile["initial_stop_pct"] / 100), 4),
        "hard_stop_price": round(price * (1 + profile["hard_stop_pct"] / 100), 4),
    }


# ============ 工具装饰器 ============

def with_retry(max_retries=MAX_RETRIES, delay=RETRY_DELAY):
    """请求失败自动重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"[{func.__name__}] 第{attempt+1}次尝试失败: {e}，{delay}秒后重试..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"[{func.__name__}] 重试{max_retries}次仍失败: {e}")
                        raise
        return wrapper
    return decorator


def with_cache(cache_type="daily"):
    """
    数据缓存装饰器
    :param cache_type: realtime / daily / quarterly / static
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 缓存键 = 函数名 + 参数hash
            cache_key = f"{func.__name__}_{hash(str(args)+str(kwargs))}"
            cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
            expire = CACHE_EXPIRE.get(cache_type, CACHE_EXPIRE["daily"])

            # 检查缓存
            if os.path.exists(cache_file):
                mtime = os.path.getmtime(cache_file)
                if time.time() - mtime < expire:
                    try:
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    except (json.JSONDecodeError, IOError):
                        pass

            # 调用原函数
            result = func(*args, **kwargs)

            # 写入缓存（尝试序列化，失败则跳过）
            try:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, default=str, indent=2)
            except (TypeError, IOError):
                pass

            return result
        return wrapper
    return decorator


# ============ 数据输出辅助 ============

def save_result(data, filename, subdir=None):
    """保存分析结果到JSON文件"""
    if subdir:
        target_dir = os.path.join(OUTPUT_DIR, subdir)
        os.makedirs(target_dir, exist_ok=True)
    else:
        target_dir = OUTPUT_DIR
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(target_dir, f"{filename}_{timestamp}.json")
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, default=str, indent=2)
    
    return filepath


def print_banner(title, char="=", width=60):
    """打印标题横幅"""
    print()
    print(char * width)
    print(f"  {title}")
    print(char * width)


def print_section(title, char="-", width=60):
    """打印小节标题"""
    print()
    print(char * width)
    print(f"  {title}")
    print(char * width)


# ============ 日期工具 ============

def today_str():
    """获取今日字符串 YYYY-MM-DD"""
    return datetime.now().strftime("%Y-%m-%d")


def n_days_ago(n):
    """获取N天前日期字符串"""
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def date_to_akshare(date_str):
    """将 YYYY-MM-DD 转为 akshare 需要的 YYYYMMDD 格式"""
    return date_str.replace("-", "")


if __name__ == "__main__":
    print_banner("配置模块自检")
    print(f"缓存目录: {CACHE_DIR}")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"今日日期: {today_str()}")
    print(f"30天前: {n_days_ago(30)}")
    print(f"行业板块数: {len(SECTOR_CODES)}")
    print(f"主要节假日数: {len(MAJOR_HOLIDAYS)}")
    print("\n✅ 配置模块OK")
