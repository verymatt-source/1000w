import requests
import os
import time
import json # 用于解析东方财富API返回的JSON数据 / 用于日志文件操作
import re # 用于解析新浪批量API返回的字符串数据
from datetime import datetime
from operator import itemgetter # 用于列表排序

# --- 全局配置 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 网页自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒
MAX_CB_PRICE = 9999.00 # 可转债计算平均价时可设置剔除价格，暂时不考虑剔除

# ======================= 通知配置区域 =======================
# 用于判断是否达到目标价位的浮点数容忍度。abs(目标比例) <= NOTIFICATION_TOLERANCE 视为触发
NOTIFICATION_TOLERANCE = 0.0005 
# 记录已发送通知的日志文件，用于实现每日只发送一次
NOTIFICATION_LOG_FILE = "notification_log.json" 
# =================================================================

# ======================= 集中配置区域 (新增/修改) =======================

# 1. 集中配置所有标的的【目标价位】
# 键必须与 TARGET_STOCKS 或 CALCULATED_TARGETS 中 config['code'] 的值保持一致。
TARGET_PRICES = {
    "399975": 700.00,  # 证券公司指数
    "USD/CNY": 6.8000, # 美元兑人民币
    "CB/AVG": 115.00   # 可转债平均价格
}

# 2. 集中配置所有标的的【备注】
# 键必须与 TARGET_STOCKS 或 CALCULATED_TARGETS 中 config['code'] 的值保持一致。
TARGET_NOTES = {
    "399975": "中证证券公司指数，低估买入，高估卖出。",
    "USD/CNY": "长期观察汇率，支撑位和压力位。",
    "CB/AVG": "可转债平均价，关注其波动性。",
}

# 3. 股票/指数配置 (TARGET_STOCKS)
# 'update_schedule' 字段定义了采集模式：
# 'MARKET': 仅在 A 股交易时间 (周一到周五 09:30-11:30, 13:00-15:00) 采集。
# '24H': 24小时采集（Actions 运行时即采集）。
TARGET_STOCKS = [
    {
        "name": "证券公司指数",
        "code": "399975",
        "type": "SZ", # 深圳指数
        "update_schedule": "MARKET" # 🚩 采集开关
    },
    # 可以在此添加更多股票或指数
]

# 4. 计算标的配置 (CALCULATED_TARGETS)
CALCULATED_TARGETS = [
    {
        "name": "美元兑人民币",
        "code": "USD/CNY",
        "api": "sina",
        "update_schedule": "24H" # 🚩 采集开关
    },
    {
        "name": "可转债平均价",
        "code": "CB/AVG",
        "api": "eastmoney",
        "update_schedule": "MARKET" # 🚩 采集开关
    }
]

# =================================================================

# ======================= 【新增功能】时间判断与运行模式映射 =======================

def is_a_share_trading_time():
    """
    判断当前北京时间是否在A股/可转债交易时段内 (周一至周五 09:30-11:30 和 13:00-15:00)。
    """
    now = datetime.now() 
    hour = now.hour
    minute = now.minute
    weekday = now.weekday() # 0=周一, 6=周日

    # 1. 周末不交易
    if weekday >= 5: 
        return False

    # 2. 判断是否在交易时间范围内
    current_time_minutes = hour * 60 + minute

    # 上午交易时段 (09:30 - 11:30)
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    if morning_start <= current_time_minutes < morning_end:
        return True

    # 下午交易时段 (13:00 - 15:00)
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    # 注意：交易结束时间 15:00 不包含，所以使用 < 即可。
    if afternoon_start <= current_time_minutes < afternoon_end: 
        return True

    return False

def map_schedule_to_display(schedule_key):
    """将配置中的运行模式键值映射为前端显示的中文文本。"""
    if schedule_key == "24H":
        return "24小时"
    elif schedule_key == "MARKET":
        return "仅交易日"
    return "未知"

# ==============================================================================

# ======================= API 采集模块 (修正美元汇率解析逻辑) =======================

def get_stock_data_from_sina(code):
    """
    从新浪 API 采集单个或批量标的数据，支持股票、指数和外汇。
    返回包含当前价 (current_price) 的字典，失败则返回 error。
    """
    if '/' in code: # 外汇，如 USD/CNY
        # 外汇API格式：list=forex_USDCNY
        full_code = code.replace('/', '') 
        url = f"http://hq.sinajs.cn/list=forex_{full_code}"
        # 匹配双引号中的所有内容
        match_pattern = re.compile(r'\"([^\"]*)\"') 
    else: # 股票或指数，如 399975
        # 股票/指数API格式：list=sz399975 (需要 type 字段，但这里为了保持简洁使用默认，如果失败则需要修改配置 type)
        url = f"http://hq.sinajs.cn/list={code}"
        match_pattern = re.compile(r'\"([^\"]*)\"')

    try:
        response = requests.get(url, timeout=5)
        response.encoding = 'gbk' # 新浪数据使用GBK编码
        data_str = response.text
        
        match = match_pattern.search(data_str)
        if match:
            values = match.group(1).split(',')
            
            if '/' in code: # 外汇 (格式：名称,现价,买入价,卖出价,昨日收盘价,开盘价,最高价,最低价,日期,时间)
                # 现价在第 2 个位置 (索引 1)。
                if len(values) > 1 and values[1].replace('.', '', 1).isdigit():
                    current_price = float(values[1])
                else:
                    return {"error": f"外汇API解析值不足/格式错误: {data_str.strip()}"}
            else: # 股票/指数 (格式：名称,开盘价,昨日收盘价,现价...)
                # 现价在第 4 个位置 (索引 3)。
                if len(values) > 3 and values[3].replace('.', '', 1).isdigit():
                    current_price = float(values[3])
                else:
                    return {"error": f"股票API解析值不足/格式错误: {data_str.strip()}"}
            
            return {
                "current_price": current_price
            }
        
        return {"error": f"API返回格式错误或无数据: {data_str.strip()}"}

    except Exception as e:
        return {"error": str(e)}


def get_cb_codes_from_eastmoney(code="CB/AVG"):
    """
    从东方财富网 API 采集所有可转债数据，计算平均价。
    """
    # 东方财富可转债数据API (所有数据)
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        'callback': 'jQuery112306263884846433555_1679051065608', 
        'sortColumns': 'TRADE_DATE',
        'sortTypes': '-1',
        'pageSize': '1000', # 确保包含所有可转债
        'pageNumber': '1',
        'reportName': 'RPT_BOND_CB_LIST',
        'columns': 'BOND_FULL_NM,CONVERT_VALUE', # 名称, 现价
        'filter': '(DELIST_FLAG="N")', # 过滤掉已退市的
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        
        # 移除 JSONP 封装层，提取 JSON 字符串
        match = re.search(r'\((\{.*\})\)', response.text)
        if not match:
            return {"error": "API返回格式错误，无法解析JSONP。"}

        json_data = json.loads(match.group(1))
        data_list = json_data['result']['data']
        
        total_price = 0
        valid_count = 0

        for item in data_list:
            price = item.get('CONVERT_VALUE')
            if price is not None and price != 0 and price < MAX_CB_PRICE:
                total_price += price
                valid_count += 1
        
        if valid_count > 0:
            avg_price = total_price / valid_count
            return {
                "current_price": avg_price,
                "count": valid_count
            }
        else:
            return {"error": "未获取到有效可转债数据"}

    except Exception as e:
        return {"error": str(e)}


# ======================= 通知与日志模块 (保持原貌) =======================

def load_notification_log():
    """从文件中加载已发送的通知日志。"""
    if os.path.exists(NOTIFICATION_LOG_FILE):
        try:
            with open(NOTIFICATION_LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_notification_log(log_data):
    """保存已发送的通知日志到文件。"""
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"保存通知日志失败: {e}")

def send_serverchan_notification(title, content):
    """
    通过 Server酱 API 发送通知。
    需要 SERVERCHAN_KEY 环境变量。
    """
    serverchan_key = os.environ.get('SERVERCHAN_KEY')
    if not serverchan_key:
        # print("未配置 SERVERCHAN_KEY 环境变量，跳过通知发送。")
        return False

    url = f"https://sctapi.ftqq.com/{serverchan_key}.send"
    data = {
        'title': title,
        'desp': content # desp 支持 Markdown
    }
    
    try:
        response = requests.post(url, data=data, timeout=5)
        response_json = response.json()
        if response_json.get('code') == 0:
            print(f"✅ 通知发送成功: {title}")
            return True
        else:
            print(f"❌ 通知发送失败: {response_json.get('message', '未知错误')}")
            return False
    except Exception as e:
        print(f"❌ Server酱请求失败: {e}")
        return False


# ======================= 主程序模块 (main) =======================

def main():
    
    # 1. 初始化日志和日期
    today_date = datetime.now().strftime('%Y-%m-%d')
    notification_log = load_notification_log()
    
    # ================= 运行模块 1：根据时间开关预处理目标 (修复标的丢失逻辑) =================
    
    is_market_open = is_a_share_trading_time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 当前A股交易时段状态: {'开放' if is_market_open else '休市'}")
    
    # 存储所有配置的列表，无论是否采集
    all_targets_config = []
    all_stock_data = [] 

    # --- 构造所有标的的配置数据结构 ---
    def prepare_all_configs(config_list, api_func_map):
        for config in config_list:
            code = config['code']
            schedule_mode = config.get("update_schedule", "MARKET") 
            
            item = {
                "name": config["name"],
                "code": code,
                "target_price": TARGET_PRICES.get(code),
                "target_ratio": None,
                "note": TARGET_NOTES.get(code, "无"),
                "schedule_mode": schedule_mode,
                "is_error": False, 
                "current_price": None, 
                "is_scheduled_skip": False, # 🚩 关键：新标记，用于区分“休市”和“采集失败”
                "api_func": api_func_map.get(code, get_stock_data_from_sina) # API func
            }
            
            # 🚩 核心修复：如果 MARKET 模式且非交易时间，则标记为跳过采集，但保留在列表中
            if schedule_mode == "MARKET" and not is_market_open:
                item["is_scheduled_skip"] = True
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 休市跳过采集 {item['name']} ({code})。")
            
            all_targets_config.append(item)
            
    # 定义API映射，用于 CALCULATED_TARGETS
    calculated_api_map = {
        "CB/AVG": get_cb_codes_from_eastmoney,
        "USD/CNY": get_stock_data_from_sina # 外汇也用sina
    }

    prepare_all_configs(TARGET_STOCKS, {})
    prepare_all_configs(CALCULATED_TARGETS, calculated_api_map)
        
    # ================= 运行模块 2：采集数据 =================

    for item in all_targets_config:
        
        # 针对休市跳过采集的标的，直接添加到最终列表，不进行API调用
        if item["is_scheduled_skip"]:
            all_stock_data.append(item)
            continue
            
        # 调用 API 采集数据
        api_data = item['api_func'](item['code']) 
        
        # 更新 item 结构
        item["is_error"] = "error" in api_data
        item["current_price"] = api_data.get("current_price")
        
        # 对于可转债，动态修改名称以显示计算基数
        if 'count' in api_data and not item['is_error']:
            item['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"
        
        all_stock_data.append(item)
        
    # ================= 运行模块 3：计算目标比例并排序 =================
    
    # 1. 计算目标比例 (Target Ratio): (当前价位 - 目标价位) / 当前价位
    for item in all_stock_data:
        # 仅对非跳过、非错误、有当前价、有目标价的标的计算比例
        if not item['is_scheduled_skip'] and not item['is_error'] and \
           item['current_price'] is not None and item['current_price'] != 0 and \
           item['target_price'] is not None:
             
            # 计算目标比例
            item['target_ratio'] = (item['current_price'] - item['target_price']) / item['current_price']
            
    # 2. 按目标比例升序排序 (从低到高)
    # 将 None 值 (无目标价、采集失败或休市) 视为最大值进行排序
    all_stock_data.sort(key=lambda x: x['target_ratio'] if x['target_ratio'] is not None else float('inf'))
    
    # ================= 运行模块 4：通知与输出 =================

    # 1. 触发通知逻辑 
    log_updated = False
    for item in all_stock_data:
        code = item['code']
        name = item['name']
        ratio = item['target_ratio']
        
        # 只有在采集成功且设置了目标价时才检查，跳过休市和采集失败的
        if item['is_scheduled_skip'] or item['is_error'] or ratio is None:
            continue
            
        # 触发条件：abs(目标比例) <= 容忍度
        is_triggered = abs(ratio) <= NOTIFICATION_TOLERANCE
        # 检查今天是否已通知过
        is_notified_today = notification_log.get(code) == today_date

        if is_triggered and not is_notified_today:
            
            # 构造通知内容 (保持了您要求的 Markdown 格式)
            title = f"【{name}】到达目标价位！！！" 
            content = (
                f"### 🎯 价格监控提醒\n\n"
                f"**标的名称：** {name}\n\n"
                f"| 指标 | 数值 |\n"
                f"| :--- | :--- |\n"
                f"| **当前价位** | {item['current_price']:.4f} |\n"
                f"| **目标价位** | {item['target_price']:.4f} |\n"
                f"| **偏离比例** | {ratio * 100:.4f} % |\n\n"
                f"--- \n\n"
                f"**策略备注：** {item.get('note', '无')}\n\n"
                f"--- \n\n"
                f"本次通知已记录（{today_date}），当日不再重复发送。"
            )
            
            # 调用通知函数
            send_success = send_serverchan_notification(title, content)
            
            # 3. 日志记录
            if send_success:
                notification_log[code] = today_date
                log_updated = True
    
    # 如果日志有更新（即成功发送了通知），则保存文件
    if log_updated:
        save_notification_log(notification_log)


    # 2. 生成 HTML
    generate_html(all_stock_data)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 脚本运行完毕，HTML 文件已更新。")


# ======================= HTML 生成模块 (generate_html) =======================

def generate_html(all_stock_data):
    """根据采集和计算后的数据生成 HTML 页面。"""

    table_rows = []

    # --- 1. 生成表格内容 ---
    # 🚩 核心修复：确保这里使用 '目标比例' 并保持原有的表格样式。
    for data in all_stock_data:
        # 默认值
        target_display = "---"
        price_display = "---"
        ratio_display = "---"
        price_color = '#7f8c8d' # 默认灰色
        ratio_color = '#7f8c8d' # 默认灰色
        
        note_display = data.get('note', '无')
        
        # 目标价显示
        if data.get('target_price') is not None:
            target_display = f"{data['target_price']:.4f}"
            
        # 🚩 区分：休市（Scheduled Skip） vs. 采集失败（API Error）
        if data.get("is_scheduled_skip"):
            price_display = "休市"
            price_color = '#7f8c8d' # 灰色
            
        elif data['is_error']:
            price_display = "采集失败"
            price_color = '#e74c3c' # 红色
        
        # 价格和比例显示 (仅在采集成功时)
        elif data['current_price'] is not None:
            price_display = f"{data['current_price']:.4f}"
            price_color = '#34495e' # 默认黑色/深色
            
            # 比例显示和颜色逻辑
            if data['target_ratio'] is not None:
                ratio_value = data['target_ratio']
                ratio_display = f"{ratio_value * 100:.2f}%"
                
                # 目标比例颜色：负数（低于目标价）绿色；正数（高于目标价）橙色
                if ratio_value < 0:
                    ratio_color = '#27ae60' # 绿色
                elif ratio_value > 0:
                    ratio_color = '#e67e22' # 橙色
                else:
                    ratio_color = '#3498db' # 蓝色（恰好等于）
            
        # 获取并格式化运行方式字段
        schedule_display = map_schedule_to_display(data.get('schedule_mode', '未知'))

        # 保持原版 HTML 结构和字段顺序，仅新增“运行方式”字段。
        row = f"""
        <tr>
            <td>{data['name']}</td>
            <td>{data['code']}</td>
            <td>{target_display}</td>
            <td style="color: {price_color}; font-weight: bold;">{price_display}</td>
            <td style="color: {ratio_color}; font-weight: bold;">{ratio_display}</td>
            <td>{schedule_display}</td> <td style="text-align: left;">{note_display}</td>
        </tr>
        """
        table_rows.append(row)

    table_content = "".join(table_rows)

    # --- 2. 完整的 HTML 模板 ---
    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="{REFRESH_INTERVAL}">
    <title>数据展示</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f4f7f9;
            color: #333;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background-color: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 10px;
            margin-top: 0;
        }}
        .timestamp {{
            color: #7f8c8d;
            font-size: 0.9em;
            margin-bottom: 20px;
            display: block;
        }}
        .styled-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 25px 0;
            font-size: 0.9em;
            text-align: center;
            min-width: 400px;
        }}
        .styled-table thead tr {{
            background-color: #3498db;
            color: #ffffff;
            text-align: center;
        }}
        .styled-table th,
        .styled-table td {{
            padding: 12px 15px;
            border: 1px solid #dddddd;
        }}
        .styled-table tbody tr {{
            border-bottom: 1px solid #dddddd;
        }}
        .styled-table tbody tr:nth-of-type(even) {{
            background-color: #f3f3f3;
        }}
        .styled-table tbody tr:last-of-type {{
            border-bottom: 2px solid #3498db;
        }}
        /* 错误行样式 */
        .styled-table tbody tr.error-row td {{
            background-color: #fde6e6; /* 浅红背景 */
            color: #c0392b; /* 深红字体 */
            font-weight: normal !important;
        }}
        .styled-table tbody tr.error-row td:first-child {{
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>价格监控报告</h1>
        <span class="timestamp">
            数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)
        </span>
        
        <table class="styled-table">
            <thead>
                <tr>
                    <th>名称</th>
                    <th>代码</th>
                    <th>目标价</th>
                    <th>当前价</th>
                    <th>目标比例</th> <th>运行方式</th> <th style="text-align: left;">备注</th>
                </tr>
            </thead>
            <tbody>
                {table_content}
            </tbody>
        </table>

        <span class="timestamp" style="margin-top: 20px; display: block;">
            刷新间隔: {REFRESH_INTERVAL} 秒（自动刷新）
        </span>
    </div>
</body>
</html>
"""
    # 写入文件
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_template)
    except Exception as e:
        print(f"写入 HTML 文件失败: {e}")

# ======================= 程序入口 =======================

if __name__ == "__main__":
    main()
