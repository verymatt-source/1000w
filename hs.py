import requests
import os
import time
import json 
import re 
from datetime import datetime
from operator import itemgetter 
import calendar 

# ======================= 1. 全局配置 (Configuration) =======================
# --- 文件/基础配置 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 网页自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒

# --- 可转债计算配置 ---
# 可转债计算平均价时，用于剔除畸高价格的上限。
MAX_CB_PRICE = 9999.00 

# --- 通知系统配置 ---
# 用于判断是否达到目标价位的浮点数容忍度。abs(目标比例) <= NOTIFICATION_TOLERANCE 视为触发。
NOTIFICATION_TOLERANCE = 0.0005 
# 记录已发送通知的日志文件，用于实现每日只发送一次。
NOTIFICATION_LOG_FILE = "notification_log.json" 

# ======================= 2. 集中配置监控标的 (MONITOR_TARGETS) =======================
# 【核心配置区】所有监控标的的信息都集中在此字典中。
# 新增/删除标的只需在此处修改。Key 为统一的标的ID/代码。
MONITOR_TARGETS = {
    
    # --- 标的 1: 证券公司指数 (新浪指数/股票API) ---
    "399975": {
        "name": "证券公司指数",
        "type": "sina_stock_or_index",       # 数据源类型: 新浪指数/股票API
        "api_code": "sz399975",              # 新浪API专用的代码 (沪市 sh, 深市 sz)
        "target_price": 700.00,             # 目标价位
        "note": "A股核心券商指数，看好长期发展。/暂无"
    },
    
    # --- 标的 2: 美元兑人民币 (新浪外汇API) ---
    "USD/CNY": {
        "name": "美元兑人民币",
        "type": "sina_forex",                # 数据源类型: 新浪外汇API
        "api_code": "fx_susdcny",            # 新浪API专用的代码 (外汇 fx_)
        "target_price": 6.8000,             # 目标价位
        "note": "人民币强势区间目标，看好升值。/暂无"
    },
    
    # --- 标的 3: 可转债平均价格 (计算型) ---
    "CB/AVG": {
        "name": "可转债平均价格",
        "type": "calculated_cb_avg",         # 数据源类型: 动态计算平均价格
        "api_code": None,                    # 计算型标的无新浪API代码
        "target_price": 115.00,             # 目标价位
        "note": "用于监控可转债整体估值水平，均价低于此值具备投资价值。/暂无"
    }
}


# ==================== 3. 通知与日志操作函数 (Notification & Logging) ====================

def load_notification_log():
    """尝试加载通知日志文件。如果文件不存在或解析失败，返回空字典。"""
    if os.path.exists(NOTIFICATION_LOG_FILE):
        try:
            with open(NOTIFICATION_LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            print("警告：无法读取或解析通知日志文件，将使用新日志。")
            return {}
    return {}

def save_notification_log(log_data):
    """
    保存通知日志文件，记录通知历史（标的ID: 日期）。
    确保日志文件在 GitHub Pages 环境下能够持久化。
    """
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=4)
        print(f"成功保存通知日志文件: {NOTIFICATION_LOG_FILE}")
    except IOError as e:
        print(f"错误：无法写入通知日志文件: {e}")


def send_serverchan_notification(title, content):
    """
    通过 Server酱 (ftqq) 发送通知。
    依赖 GitHub Actions 注入的 SERVERCHAN_SCKEY 环境变量。
    
    Args:
        title (str): 通知标题。
        content (str): 通知内容（支持 Markdown）。
    Returns:
        bool: 通知是否发送成功。
    """
    SCKEY = os.environ.get('SERVERCHAN_SCKEY')
    
    if not SCKEY:
        print("警告：未找到 SERVERCHAN_SCKEY 环境变量，通知功能跳过。")
        return False

    url = f"https://sctapi.ftqq.com/{SCKEY}.send"
    data = {"title": title, "desp": content}
    
    try:
        response = requests.post(url, data=data, timeout=5)
        response.raise_for_status() 
        result = response.json()
        
        if result.get('code') == 0:
            print("Server酱通知发送成功。")
            return True
        else:
            print(f"Server酱通知发送失败：{result.get('message')}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Server酱通知发送失败 (网络错误): {e}")
        return False
    except Exception as e:
        print(f"Server酱通知发送失败 (未知错误): {e}")
        return False


# ==================== 4. 数据采集函数 (Data Fetchers) ====================

def get_data_sina(stock_api_code):
    """
    使用新浪财经API获取指定证券或外汇的实时价格。
    Args:
        stock_api_code (str): 新浪API专用代码 (如 'sz399975', 'fx_susdcny')。
    Returns:
        dict: 包含 current_price, open_price, prev_close 或 error 信息的字典。
    """
    url = f"http://hq.sinajs.cn/list={stock_api_code}"
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'http://finance.sina.com.cn/'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10) 
        response.encoding = 'gbk'
        data = response.text
        
        if response.status_code != 200 or '="' not in data:
            return {"error": "获取失败", "detail": f"HTTP状态码: {response.status_code}"}

        # 从 var hq_str_xxxx="......." 中提取报价部分
        data_content = data.split('="')[1].strip('";')
        parts = data_content.split(',')
        
        # 兼容性处理: 外汇价格是第9项 (parts[8])；股票/指数价格是第4项 (parts[3])
        price_index = 8 if stock_api_code.startswith('fx_') else 3
        
        if len(parts) < max(price_index, 3) + 1:
            return {"error": "解析失败", "detail": "数据项不足"}
            
        current_price = parts[price_index]
        
        # 验证价格数据有效性
        if current_price and current_price.replace('.', '', 1).isdigit():
            return {
                "current_price": float(current_price),
                "open_price": float(parts[1]) if len(parts) > 1 and parts[1].replace('.', '', 1).isdigit() else None,
                "prev_close": float(parts[2]) if len(parts) > 2 and parts[2].replace('.', '', 1).isdigit() else None,
            }
        else:
            return {"error": "解析失败", "detail": "价格数据无效或无法转换为浮点数"}
            
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}


def get_cb_codes_from_eastmoney():
    """
    通过爬取东方财富接口，动态获取所有正在交易中的可转债代码列表。
    将代码转换为新浪 API 格式 (sh11xxxx / sz12xxxx)。
    Returns:
        tuple: (codes_list, error_msg)
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=SECURITY_CODE&sortTypes=-1&pageSize=1000&pageNumber=1&reportName=RPT_BOND_CB_LIST&columns=SECURITY_CODE"
    
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://data.eastmoney.com/kzz/default.html'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return [], f"HTTP错误：状态码 {response.status_code}"
            
        data = response.json()
        if data.get('code') != 0:
            return [], f"东方财富API返回错误：{data.get('message', '未知错误')}"
            
        codes_list = []
        for item in data['result']['data']:
            code = str(item['SECURITY_CODE'])
            # 交易所前缀判断
            if code.startswith('11') or code.startswith('13') or code.startswith('14'):
                sina_code = f"sh{code}" # 沪市可转债
            elif code.startswith('12'):
                sina_code = f"sz{code}" # 深市可转债
            else:
                continue
            codes_list.append(sina_code)
            
        return codes_list, None
        
    except requests.exceptions.RequestException as e:
        return [], f"网络错误：{str(e)}"
    except json.JSONDecodeError:
        return [], "数据解析失败：返回内容不是有效的 JSON"
    except Exception as e:
        return [], f"未知错误：{str(e)}"


def get_cb_avg_price_from_list(codes_list):
    """
    通过新浪 API 批量获取可转债价格，并计算有效价格的平均值。
    Args:
        codes_list (list): 新浪 API 格式的代码列表，如 ['sh11xxxx', 'sz12xxxx']。
    Returns:
        dict: 包含 current_price, count (用于计算的标的数) 或 error 信息的字典。
    """
    global MAX_CB_PRICE
    
    if not codes_list:
        return {"error": "计算失败", "detail": "可转债代码列表为空，无法进行计算。"}

    query_string = ",".join(codes_list)
    url = f"http://hq.sinajs.cn/list={query_string}" 
    
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'http://finance.sina.com.cn/' 
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = 'gbk'
        data = response.text
        
        if response.status_code != 200 or not data.strip():
            return {"error": "获取失败", "detail": f"新浪API状态码: {response.status_code}"}
        
        valid_lines = [line for line in data.split('\n') if line.startswith('var hq_str_')]
        prices = []
        
        for line in valid_lines:
            match = re.search(r'="(.+?)"', line)
            if match:
                parts = match.group(1).split(',')
                # 可转债的实时价格位于第4个位置 (parts[3])
                if len(parts) > 3:
                    price_str = parts[3] 
                    
                    if price_str and price_str.replace('.', '', 1).isdigit():
                        price_float = float(price_str)
                        
                        # 剔除逻辑：只纳入价格大于0且低于 MAX_CB_PRICE 的标的进行计算
                        if price_float > 0 and price_float < MAX_CB_PRICE:
                            prices.append(price_float)
        
        if not prices:
            return {"error": "计算失败", "detail": f"已获取 {len(codes_list)} 个代码，但新浪未返回有效或低于 {MAX_CB_PRICE:.2f} 的价格数据。"}

        avg_price = sum(prices) / len(prices)
        
        return {
            "current_price": avg_price,
            "open_price": None, 
            "prev_close": None, 
            "count": len(prices) # 实际用于计算的有效数量
        }
            
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": f"数据处理异常: {str(e)}"}


def calculate_cb_avg_price():
    """统一的可转债平均价格计算入口，用于主循环调用，封装了代码获取和价格计算两个步骤。"""
    codes_list, error_msg = get_cb_codes_from_eastmoney()
    
    if error_msg:
        return {"error": "代码列表获取失败", "detail": error_msg}
    else:
        api_data = get_cb_avg_price_from_list(codes_list)
        return api_data


# ==================== 5. 辅助函数 (Helpers) ====================

def is_trading_time():
    """
    判断当前时间是否处于中国证券市场的正常交易时段 (北京时间)。
    不考虑法定节假日，只判断周一至周五 9:30-11:30 和 13:00-15:00。
    """
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    weekday = now.weekday() # Monday is 0 and Sunday is 6
    
    # 1. 判断是否为工作日 (周一到周五)
    if weekday >= 5: 
        return False
        
    # 2. 判断是否处于交易时段
    am_start = 9 * 60 + 30
    am_end = 11 * 60 + 30
    pm_start = 13 * 60 + 0
    pm_end = 15 * 60 + 0
    
    current_minutes = hour * 60 + minute
    
    if (current_minutes >= am_start and current_minutes <= am_end) or \
       (current_minutes >= pm_start and current_minutes <= pm_end):
        return True
        
    return False

def format_price(code, price):
    """
    根据标的代码格式化价格显示，例如汇率保留4位小数，其他保留3位。
    Args:
        code (str): 标的ID。
        price (float/None): 价格。
    Returns:
        str: 格式化后的价格字符串。
    """
    if price is None:
        return "N/A"
    if code == 'USD/CNY':
        return f"{price:.4f}"
    elif code == 'CB/AVG':
        return f"{price:.3f}"
    else:
        return f"{price:.3f}"
        
def calculate_ratio_and_sort(data_list):
    """
    计算目标比例并按升序（从低到高）排序。
    目标比例计算公式：(当前价位 - 目标价位) / 当前价位。负数代表低于目标价。
    Args:
        data_list (list): 包含所有标的原始数据的列表。
    Returns:
        list: 包含 'target_ratio' 字段并已排序的列表。
    """
    for item in data_list:
        item['target_ratio'] = None 
        
        if not item['is_error'] and item['current_price'] is not None and item['current_price'] != 0:
            current_price = item['current_price']
            target_price = item['target_price']
            
            # 目标比例计算: (当前价位 - 目标价位) / 当前价位
            item['target_ratio'] = (current_price - target_price) / current_price
            
    # 按照目标比例升序排序 (None 值/错误数据排在最后)
    data_list.sort(key=lambda x: x['target_ratio'] if x['target_ratio'] is not None else float('inf'))
    return data_list


# ==================== 6. HTML 生成函数 (HTML Generation) ====================
def create_html_content(stock_data_list):
    """
    生成带有价格表格、目标比例和自动刷新功能的HTML内容。
    Args:
        stock_data_list (list): 包含所有监控标的采集结果的列表。
    Returns:
        str: 完整的 HTML 字符串。
    """
    global MAX_CB_PRICE
    global REFRESH_INTERVAL
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    table_rows = []
    
    # 判断交易时间状态，并添加到时间戳后面
    if is_trading_time():
        status_text = '<span style="color: #27ae60;">交易时间 (正常运行)</span>' 
    else:
        status_text = '非交易时间' 
        
    timestamp_with_status = f"{timestamp} | {status_text}"
    
    # 表格头部
    table_rows.append("""
        <tr>
            <th>标的名称</th>
            <th>证券代码</th>
            <th>目标价位</th>
            <th>当前价位</th>
            <th>目标比例</th> 
            <th>备注</th>
        </tr>
    """)
    
    for data in stock_data_list:
        
        price_color = '#27ae60' 
        ratio_color = '#7f8c8d'
        target_display = format_price(data['code'], data['target_price'])
        price_display = "N/A"
        ratio_display = "N/A"
        note_display = data.get('note', '')

        if data['is_error']:
            # 错误信息显示为红色，并显示详情
            price_display = f"数据错误: {data.get('detail', '未知错误')}"
            price_color = '#e74c3c'
        else:
            # 1. 价格格式化
            price_display = format_price(data['code'], data['current_price'])
                
            # 2. 当前价位颜色判断
            if data['current_price'] >= data['target_price']:
                price_color = '#e67e22' # 橙色 (高于/等于目标价，风险/卖出区域)
            else:
                price_color = '#27ae60' # 绿色 (低于目标价，机会/买入区域)

            # 3. 目标比例显示和颜色判断
            if data.get('target_ratio') is not None:
                ratio_value = data['target_ratio']
                ratio_display = f"{ratio_value * 100:.2f}%"
                
                # 目标比例颜色：负数（低于目标价）绿色；正数（高于目标价）橙色
                if ratio_value < 0:
                    ratio_color = '#27ae60' 
                elif ratio_value > 0:
                    ratio_color = '#e67e22'
                else:
                    ratio_color = '#3498db'
            
        # 生成表格行
        row = f"""
        <tr>
            <td>{data['name']}</td>
            <td>{data['code']}</td>
            <td>{target_display}</td>
            <td style="color: {price_color}; font-weight: bold;">{price_display}</td>
            <td style="color: {ratio_color}; font-weight: bold;">{ratio_display}</td>
            <td style="text-align: left;">{note_display}</td>
        </tr>
        """
        table_rows.append(row)

    table_content = "".join(table_rows)

    # --- 完整的 HTML 模板 (CSS 优化，确保可读性) ---
    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="{REFRESH_INTERVAL}">
    <title>价格监控看板</title>
    <meta name="robots" content="noindex, nofollow">
    <style>
        /* 基础样式 */
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; margin-top: 50px; background-color: #f4f4f9; }}
        h1 {{ color: #2c3e50; font-size: 2.5em; }}
        
        /* 表格样式 */
        table {{ 
            width: 95%; 
            margin: 30px auto; 
            border-collapse: collapse; 
            box-shadow: 0 4px 12px rgba(0,0,0,0.15); /* 阴影更明显 */
            background-color: white;
            border-radius: 8px; /* 圆角 */
            overflow: hidden; /* 确保圆角可见 */
        }}
        th, td {{ 
            border: 1px solid #e0e0e0; /* 边框更淡 */
            padding: 15px; 
            text-align: center;
            font-size: 1.0em;
        }}
        th {{ 
            background-color: #3498db; 
            color: white; 
            font-weight: bold; 
            text-transform: uppercase;
        }}
        td:last-child {{
            text-align: left; /* 备注列左对齐 */
            max-width: 300px;
            white-space: normal; /* 允许换行 */
        }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }} /* 斑马纹 */
        tr:hover {{ background-color: #f0f0f0; }} /* 悬停效果 */
        
        /* 时间戳和备注 */
        .timestamp {{ color: #7f8c8d; margin-top: 30px; font-size: 1.2em; font-weight: 600;}}
        .note p {{ color: #34495e; margin: 5px 0; font-size: 1em;}}
    </style>
</head>
<body>
    <h1>价格监控看板 (按偏离目标比例排序)</h1>
    
    <table>
        {table_content}
    </table>

    <div class="timestamp">数据更新时间: {timestamp_with_status}</div>
    <div class="note">
        <p>📌 **代码运行时间说明**：本代码由 GitHub Actions 在**交易日**的**交易时段**内运行。</p>
        <p>📌 **可转债均价计算说明**：均价计算已**剔除**价格大于或等于 {MAX_CB_PRICE:.2f} 的标的。</p>
        <p>注意：本页面每 {REFRESH_INTERVAL // 60} 分钟自动重新加载，以获取最新数据。</p>
    </div>
</body>
</html>
"""
    return html_template

# ==================== 7. 主逻辑 (Main Execution) ====================
if __name__ == "__main__":
    
    all_stock_data = []
    
    # --- 1. 遍历集中配置，按数据源类型采集数据 ---
    print("--- 1. 开始采集数据 ---")
    for code, config in MONITOR_TARGETS.items():
        
        api_data = {}
        
        # A. 新浪指数/股票/外汇 (统一调用 get_data_sina)
        if config['type'] in ["sina_stock_or_index", "sina_forex"]:
            api_data = get_data_sina(config['api_code'])
            
        # B. 计算型标的（可转债平均价格）
        elif config['type'] == "calculated_cb_avg":
            api_data = calculate_cb_avg_price()

        # C. 组装最终数据结构，用于后续处理和 HTML 输出
        final_data = {
            "name": config["name"],
            "code": code,
            "target_price": config["target_price"], 
            "note": config["note"],         
            "is_error": "error" in api_data,
            "current_price": api_data.get("current_price"),
            **api_data # 包含 detail, error, count 等信息
        }
        
        # 修正计算型标的名称，加入数量，增强信息展示
        if config['type'] == "calculated_cb_avg" and 'count' in api_data and not final_data['is_error']:
            final_data['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"
            
        all_stock_data.append(final_data)
        
    # --- 2. 计算目标比例并按升序排序 (从低于目标价到高于目标价) ---
    all_stock_data = calculate_ratio_and_sort(all_stock_data)


    # --- 3. 目标价位通知逻辑 (基于日志文件实现每日单次通知) ---
    print("--- 3. 正在检查目标价位通知 ---")
    
    today_date = datetime.now().strftime('%Y-%m-%d')
    notification_log = load_notification_log() 
    log_updated = False 

    for item in all_stock_data:
        code = item.get('code')
        name = item.get('name')
        ratio = item.get('target_ratio')
        
        # 仅对有效数据进行判断
        if item['is_error'] or ratio is None:
            continue
            
        # 触发条件：偏离目标比例在容忍度范围内 (绝对值小于等于容忍度)
        is_triggered = abs(ratio) <= NOTIFICATION_TOLERANCE
        # 防重发判断：检查今天是否已经发送过
        is_notified_today = notification_log.get(code) == today_date

        if is_triggered and not is_notified_today:
            
            # 通知标题和内容 (使用 Markdown 表格，更清晰)
            title = f"【{name}】已到达目标价位！！！" 
            content = (
                f"### 🎯 价格监控提醒\n\n"
                f"**标的名称：** {name}\n\n"
                f"| 指标 | 数值 |\n"
                f"| :--- | :--- |\n"
                f"| **当前价位** | {format_price(code, item['current_price'])} |\n"
                f"| **目标价位** | {format_price(code, item['target_price'])} |\n"
                f"| **偏离比例** | {ratio * 100:.4f} % |\n\n"
                f"--- \n\n"
                f"**策略备注：** {item.get('note', '无')}\n\n"
                f"--- \n\n"
                f"本次通知已记录（{today_date}），当日不再重复发送。"
            )
            
            send_success = send_serverchan_notification(title, content)
            
            # 记录通知日志
            if send_success:
                notification_log[code] = today_date
                log_updated = True
    
    # 保存日志文件 (只有当有新的通知发送时才写入，减少 I/O)
    if log_updated:
        save_notification_log(notification_log)


    # --- 4. 生成 HTML 文件 (前端展示) ---
    print("--- 4. 正在生成 HTML 文件 ---")
    try:
        html_content = create_html_content(all_stock_data)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个标的数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")
