import requests
import os
import json
import re
import calendar
from datetime import datetime
from operator import itemgetter
from typing import List, Dict, Any, Optional, Tuple, Union

# --- 🎯 核心配置常量 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 网页自动刷新时间（秒）。30分钟 = 1800秒
MAX_CB_PRICE = 9999.00  # 可转债计算平均价时，用于剔除畸高价格的上限，目前设为不剔除
NOTIFICATION_TOLERANCE = 0.0005  # 用于判断是否达到目标价位的浮点数容忍度。
NOTIFICATION_LOG_FILE = "notification_log.json" # 记录已发送通知的日志文件
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY") # Server酱通知密钥，从环境变量获取

# --- 🚀 统一标的配置 (单一配置源) ---
# 增加、删减标的，只需修改以下列表。
#
# 结构:
# {
#     "code": str,                # 标的唯一代码 (用于日志/唯一标识)
#     "name": str,                # 显示名称
#     "source_type": str,         # 数据来源/类型: "SINA_API", "CB_AVG_PRICE"
#     "api_code": Optional[str],  # (仅 SINA_API): 实际用于新浪API请求的代码 (e.g., "sz399975")
#     "target_price": float,      # 目标价位 (触发通知的阈值)
#     "note": Optional[str]       # 策略备注信息
# }
ALL_TARGETS_CONFIG: List[Dict[str, Union[str, float, None]]] = [
    # ------------------- 1. 新浪API/指数/外汇 -------------------
    {
        "code": "399975",
        "name": "证券公司指数",
        "source_type": "SINA_API",
        "api_code": "sz399975",
        "target_price": 700.00,
        "note": "证券是牛市先锋，在700点以下积极定投，目标价位是未来牛市卖点。",
    },
    {
        "code": "000001",
        "name": "上证指数",
        "source_type": "SINA_API",
        "api_code": "sh000001",
        "target_price": 3500.00,
        "note": "大盘关键点位监控。",
    },
    {
        "code": "USD/CNY",
        "name": "美元/人民币",
        "source_type": "SINA_API",
        "api_code": "fx_susdcny",
        "target_price": 6.80,
        "note": "人民币升值目标价位。",
    },
    # ------------------- 2. 自定义计算标的 -------------------
    {
        "code": "CB/AVG",
        "name": "可转债平均价",
        "source_type": "CB_AVG_PRICE",
        "api_code": None, # 无需API代码，计算逻辑内部处理
        "target_price": 105.00,
        "note": "基于东方财富所有可转债的算术平均价，低于105是极佳的低吸区域。",
    },
]


# --- 🛠 实用工具函数 ---

def is_trading_day() -> bool:
    """
    判断当前是否为工作日 (周一至周五)。
    此函数仅判断日期，不考虑法定节假日或休市。
    
    Returns:
        bool: 如果是周一到周五则返回 True。
    """
    today = datetime.now()
    # calendar.weekday() 返回 0 (周一) 到 6 (周日)
    return calendar.weekday(today.year, today.month, today.day) < 5

def load_notification_log() -> Dict[str, str]:
    """
    尝试从 JSON 文件加载通知日志。
    日志结构: {"code": "YYYY-MM-DD"}
    
    Returns:
        Dict[str, str]: 加载的日志字典；文件不存在或解析失败则返回空字典。
    """
    if os.path.exists(NOTIFICATION_LOG_FILE):
        try:
            with open(NOTIFICATION_LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"WARN: 日志文件 {NOTIFICATION_LOG_FILE} 解析失败，将使用新的空日志。")
            return {}
    return {}

def save_notification_log(log_data: Dict[str, str]):
    """
    将通知日志保存到 JSON 文件。
    
    Args:
        log_data (Dict[str, str]): 要保存的日志数据。
    """
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=4)
        print(f"INFO: 成功保存通知日志到 {NOTIFICATION_LOG_FILE}")
    except Exception as e:
        print(f"ERROR: 保存通知日志失败: {e}")

# --- 🌐 数据采集 API 函数 ---

def get_data_sina(stock_api_code: str) -> Optional[Dict[str, float]]:
    """
    从新浪财经API获取单个标的（股票/指数/外汇）的实时数据。
    
    Args:
        stock_api_code (str): 带前缀的股票代码 (e.g., "sh000001", "fx_susdcny")。
        
    Returns:
        Optional[Dict[str, float]]: 包含 'current_price' 和 'prev_close' 的字典，
                                    如果获取失败则返回 None。
    """
    url = f"http://hq.sinajs.cn/list={stock_api_code}"
    try:
        response = requests.get(url, timeout=5)
        response.encoding = 'gbk' # 新浪API返回是GBK编码
        data = response.text
        
        # 使用正则表达式匹配和提取数据
        match = re.search(r'="([^,"]+),([^,"]+),([^,"]+),([^,"]+)', data)
        if match:
            # 股票/指数: [3]是当前价，[2]是昨日收盘价
            # 外汇: [1]是当前价，[2]是昨日收盘价
            
            # 判断是否为外汇（通过 fx_ 前缀判断）
            if stock_api_code.startswith("fx_"):
                current_price = float(match.group(1))
                prev_close = float(match.group(2))
            else:
                current_price = float(match.group(4))
                prev_close = float(match.group(3))
                
            return {
                'current_price': current_price,
                'prev_close': prev_close
            }
        
    except Exception as e:
        print(f"ERROR: 获取新浪API数据失败 ({stock_api_code}): {e}")
        
    return None

def get_data_sina_batch(api_codes: List[str]) -> Dict[str, float]:
    """
    批量获取新浪财经API数据。
    
    Args:
        api_codes (List[str]): 一组带前缀的股票代码列表。
        
    Returns:
        Dict[str, float]: 字典，键为股票代码（不带前缀），值为当前价。
    """
    if not api_codes:
        return {}
        
    codes_str = ",".join(api_codes)
    url = f"http://hq.sinajs.cn/list={codes_str}"
    results = {}
    
    try:
        response = requests.get(url, timeout=10)
        response.encoding = 'gbk'
        lines = response.text.strip().split(';')
        
        for line in lines:
            if line and "=" in line:
                match = re.search(r'hq_str_(\w+)="[^,"]+,[^,"]+,[^,"]+,([^,"]+)', line)
                if match:
                    # 匹配到的 group(1) 是完整代码，group(2) 是当前价
                    full_code = match.group(1)
                    price = float(match.group(2))
                    results[full_code] = price
                    
    except Exception as e:
        print(f"ERROR: 批量获取新浪API数据失败: {e}")
        
    return results

def get_cb_codes_from_eastmoney() -> List[str]:
    """
    从东方财富网API动态获取当前所有可转债的股票代码。
    
    Returns:
        List[str]: 可转债API代码列表 (e.g., ["sh110047", "sz128108"])。
    """
    url = "http://datacenter-web.eastmoney.com/api/data/v1/get"
    # 此处的字段和参数经过精简和优化，仅获取代码和市场信息。
    params = {
        "reportName": "RPT_BOND_CB_LIST",
        "columns": "SECURITY_CODE,SNAME",
        "pageSize": 500, # 确保能获取所有可转债
        "pageNumber": 1,
        "sortTypes": "-1",
        "sortColumns": "TRADE_DATE",
        "source": "WEB",
        "client": "WEB",
    }
    
    codes = []
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data and data['code'] == 0 and data['data']:
            for item in data['data']['list']:
                # 市场代码判断：上交所 '1', '5', '6', '9' 开头；深交所 '0', '2', '3' 开头
                # 转债代码通常是 1 开头 (上交所) 或 12 开头 (深交所)
                code = item['SECURITY_CODE']
                if code.startswith('1'):
                    # 上海转债代码，通常为 sh1xxxxx
                    codes.append(f"sh{code}")
                elif code.startswith('12'):
                    # 深圳转债代码，通常为 sz12xxxx
                    codes.append(f"sz{code}")
            
            print(f"INFO: 成功从东方财富获取 {len(codes)} 个可转债代码。")
            return codes
            
    except Exception as e:
        print(f"ERROR: 获取可转债代码失败: {e}")
        
    return []

def get_cb_avg_price() -> Optional[Dict[str, float]]:
    """
    计算所有可转债的算术平均价格。
    
    Returns:
        Optional[Dict[str, float]]: 包含 'current_price' 和 'prev_close' (此处为0) 的字典，
                                    计算失败则返回 None。
    """
    cb_codes = get_cb_codes_from_eastmoney()
    if not cb_codes:
        return None
        
    # 批量获取价格，返回 {full_code: price} 字典
    price_map = get_data_sina_batch(cb_codes)
    
    valid_prices = []
    for full_code, price in price_map.items():
        # 排除价格过高或无法交易的数据（新浪API如果无法获取价格，可能返回0）
        if 0 < price < MAX_CB_PRICE:
            valid_prices.append(price)

    if not valid_prices:
        print("WARN: 未获取到有效的可转债价格数据，无法计算平均价。")
        return None

    avg_price = sum(valid_prices) / len(valid_prices)
    
    print(f"INFO: 基于 {len(valid_prices)} 个可转债，计算平均价为 {avg_price:.4f}")
    
    # 可转债平均价没有“昨日收盘价”的概念，设为0.0
    return {
        'current_price': avg_price,
        'prev_close': 0.0
    }

# --- 🚀 核心抽象函数 ---

def fetch_target_data(config: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    根据配置的 source_type 字段，获取标的实时数据。
    
    Args:
        config (Dict[str, Any]): 单个标的的统一配置字典。
        
    Returns:
        Optional[Dict[str, float]]: 包含 'current_price' 和 'prev_close' 的字典，
                                    获取失败则返回 None。
    """
    source_type = config['source_type']
    api_code = config.get('api_code')
    
    if source_type == "SINA_API" and api_code:
        return get_data_sina(api_code)
        
    elif source_type == "CB_AVG_PRICE":
        return get_cb_avg_price()
        
    else:
        print(f"WARN: 未知的或配置不完整的标的类型: {config['code']}")
        return None

# --- 📢 通知函数 ---

def send_serverchan_notification(title: str, content: str) -> bool:
    """
    通过 Server酱 发送通知。
    
    Args:
        title (str): 通知标题。
        content (str): 通知内容，支持 Markdown。
        
    Returns:
        bool: 发送成功返回 True，否则返回 False。
    """
    if not SERVERCHAN_KEY:
        print("WARN: 环境变量 SERVERCHAN_KEY 未设置，跳过通知发送。")
        return False
        
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = {
        'title': title,
        'desp': content # desp字段支持Markdown
    }
    
    try:
        response = requests.post(url, data=data, timeout=5)
        result = response.json()
        if result['code'] == 0:
            print(f"INFO: Server酱通知发送成功: {title}")
            return True
        else:
            print(f"ERROR: Server酱通知发送失败。返回信息: {result.get('message', '未知错误')}")
            return False
            
    except Exception as e:
        print(f"ERROR: Server酱请求失败: {e}")
        return False

# --- ⚙️ 主逻辑与渲染 ---

def process_and_render():
    """
    程序主入口：
    1. 收集所有标的数据。
    2. 检查并发送通知。
    3. 生成 HTML 文件。
    """
    if not is_trading_day():
        print("INFO: 当前非工作日，不执行数据采集和通知。")
        # 即使非交易日，也应该运行一次，确保日志能保存。
        # pass

    all_results: List[Dict[str, Any]] = []
    log_updated = False
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    # 1. 加载通知日志，用于状态持久化
    notification_log = load_notification_log()
    
    # 2. 遍历统一配置列表，获取数据并处理通知
    for config in ALL_TARGETS_CONFIG:
        code = config['code']
        name = config['name']
        target_price = config['target_price']
        
        # 抽象化数据获取
        data = fetch_target_data(config)
        
        if data:
            current_price = data['current_price']
            prev_close = data['prev_close']
            
            # 计算偏离比例
            if target_price > 0:
                # 目标价位>当前价位 触发，计算 (目标价 - 当前价) / 目标价
                ratio = (target_price - current_price) / target_price
            elif target_price < 0:
                # 目标价位<0 (例如，盈利卖出价) 触发，计算 (当前价 - 目标价) / abs(目标价)
                # 注：此处逻辑需要用户确保 target_price < 0 时，是期望 current_price > target_price 触发
                ratio = (current_price - target_price) / abs(target_price)
            else:
                # 目标价为0，无意义，不计算
                ratio = 0.0

            # 检查是否触发通知
            is_triggered = ratio >= 0 and abs(ratio) <= NOTIFICATION_TOLERANCE
            is_notified_today = notification_log.get(code) == today_date
            
            if is_triggered and not is_notified_today:
                print(f"ALERT: 标的【{name}】触发目标价位通知！")
                
                # --- 📢 通知内容优化 ---
                title = f"【{name}】已到达目标价位！" 
                content = (
                    f"### 🎯 价格监控提醒\n\n"
                    f"**标的名称：** {name}\n\n"
                    f"| 指标 | 数值 |\n"
                    f"| :--- | :--- |\n"
                    f"| **当前价位** | {current_price:.4f} |\n"
                    f"| **目标价位** | {target_price:.4f} |\n"
                    f"| **目标偏离** | {ratio * 100:.4f} % |\n\n"
                    f"--- \n\n"
                    f"**策略备注：** {config.get('note', '无')}\n\n"
                    f"--- \n\n"
                    f"本次通知已记录（{today_date}），当日不再重复发送。"
                )
                
                send_success = send_serverchan_notification(title, content)
                
                # 记录通知日志
                if send_success:
                    notification_log[code] = today_date
                    log_updated = True
            
            # 将结果添加到列表
            all_results.append({
                'code': code,
                'name': name,
                'current_price': current_price,
                'prev_close': prev_close,
                'target_price': target_price,
                'ratio': ratio,
                'note': config.get('note', ''),
                'is_notified_today': is_notified_today
            })

    # 3. 如果日志有更新（即成功发送了通知），则保存文件
    if log_updated:
        save_notification_log(notification_log)
        
    # 4. 生成 HTML 报告
    if all_results:
        # 按偏离比例升序排列，目标偏离越小的越靠前 (即越接近目标价)
        sorted_results = sorted(all_results, key=itemgetter('ratio'))
        generate_html_report(sorted_results)
    else:
        print("WARN: 未获取到任何有效数据，跳过 HTML 报告生成。")


def generate_html_report(results: List[Dict[str, Any]]):
    """
    根据数据结果生成自刷新 HTML 报告文件。
    
    Args:
        results (List[Dict[str, Any]]): 待展示的标的数据列表。
    """
    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    
    # HTML 内容拼接
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="{REFRESH_INTERVAL}">
    <title>价格监控报告 - {current_time}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }}
        .container {{ max-width: 1000px; margin: 0 auto; background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); }}
        h1 {{ color: #1e88e5; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .update-time {{ color: #777; font-size: 0.9em; margin-bottom: 20px; display: block; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #e3f2fd; color: #1e88e5; font-weight: 600; }}
        tr:hover {{ background-color: #f1f8e9; }}
        .note {{ font-size: 0.8em; color: #666; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .price-up {{ color: #e53935; font-weight: bold; }}
        .price-down {{ color: #43a047; font-weight: bold; }}
        .price-flat {{ color: #555; }}
        .ratio-alert {{ background-color: #fffde7; font-weight: bold; }}
        .ratio-ok {{ background-color: #e8f5e9; }}
        .ratio-near {{ background-color: #fff3e0; }}
        .ratio-far {{ background-color: #f3f3f3; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 价格监控实时报告</h1>
        <span class="update-time">更新时间：{current_time} | 下次刷新: {REFRESH_INTERVAL}秒后</span>
        
        <table>
            <thead>
                <tr>
                    <th>名称 (代码)</th>
                    <th>当前价</th>
                    <th>今日涨跌幅</th>
                    <th>目标价</th>
                    <th>目标偏离 (绝对值)</th>
                    <th>备注</th>
                </tr>
            </thead>
            <tbody>
"""
    
    for item in results:
        # 计算涨跌幅
        if item['prev_close'] > 0:
            change_percent = (item['current_price'] - item['prev_close']) / item['prev_close'] * 100
            change_class = "price-up" if change_percent > 0 else ("price-down" if change_percent < 0 else "price-flat")
            change_text = f"{change_percent:.2f}%"
        else:
            # 对于可转债平均价等无昨日收盘价的，显示N/A
            change_class = "price-flat"
            change_text = "N/A"

        # 根据偏离度设置背景色
        abs_ratio = abs(item['ratio'])
        if abs_ratio <= NOTIFICATION_TOLERANCE * 5: # 极度接近/触发
             ratio_class = "ratio-alert" 
        elif abs_ratio < 0.01: # 1%以内
             ratio_class = "ratio-near" 
        elif abs_ratio < 0.05: # 5%以内
             ratio_class = "ratio-ok"
        else:
             ratio_class = "ratio-far"
             
        # 格式化目标偏离度
        ratio_display = f"{abs_ratio * 100:.4f}%"
        
        # 目标价位是否触发（辅助提醒）
        triggered_icon = "⭐" if item['is_notified_today'] else ""
        
        html_content += f"""
                <tr>
                    <td>{item['name']} ({item['code']})</td>
                    <td>{item['current_price']:.4f} {triggered_icon}</td>
                    <td class="{change_class}">{change_text}</td>
                    <td>{item['target_price']:.4f}</td>
                    <td class="{ratio_class}">{ratio_display}</td>
                    <td class="note" title="{item['note']}">{item['note']}</td>
                </tr>
"""

    html_content += """
            </tbody>
        </table>
        
        <p class="update-time" style="margin-top: 30px;">
            **数据来源:** 新浪财经 / 东方财富网 (可转债) | 
            **通知状态:** ⭐ 表示当日已发送通知 (记录在 notification_log.json)。
        </p>
    </div>
</body>
</html>
"""

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"INFO: 成功生成 HTML 报告: {OUTPUT_FILE}")
    except Exception as e:
        print(f"ERROR: HTML 报告生成失败: {e}")

if __name__ == '__main__':
    # 在实际运行时，可以在主函数外执行，以确保日志文件被正确加载和保存。
    # process_and_render()
    # 增加一个简单的执行判断，确保在无 SERVERCHAN_KEY 时可以进行页面生成。
    if not SERVERCHAN_KEY:
        print("WARN: SERVERCHAN_KEY 环境变量缺失，将仅运行数据采集和页面生成，跳过通知检查。")
        
    process_and_render()
