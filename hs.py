import requests
import os
import time # 未在代码中使用，但保留导入
import json # 用于解析东方财富API返回的JSON数据 / 用于日志文件操作
import re # 用于解析新浪批量API返回的字符串数据
from datetime import datetime
from operator import itemgetter # 用于列表排序
import calendar # 用于判断周末/交易日

# --- 全局配置 (保持不变) ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 网页自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒
MAX_CB_PRICE = 9999.00 # 可转债计算平均价时可设置剔除价格，暂时不考虑剔除

# ======================= 通知配置区域 (保持不变) =======================
NOTIFICATION_TOLERANCE = 0.0005 
NOTIFICATION_LOG_FILE = "notification_log.json" 
# =====================================================================

# ======================= 【核心优化区域】集中配置所有标的 =======================

# ALL_TARGET_CONFIGS 集中了所有标的的配置信息。
# key: 内部唯一代码 (用于日志、通知、排序)
# type: 定义数据来源和处理方式。'SINA' (新浪 API), 'CB_AVG' (可转债平均价格计算)
# api_code: 实际用于新浪API查询的代码 (对于 'CB_AVG' 类型，此字段可忽略)。

ALL_TARGET_CONFIGS = {
    # 证券公司指数
    "399975": {
        "name": "证券公司指数",
        "type": "SINA", 
        "api_code": "sz399975",
        "target_price": 700.00,  # 目标价位 (集中)
        "note": "/暂无"         # 备注 (集中)
    }, 
    
    # 美元兑人民币汇率
    "USD/CNY": {
        "name": "美元兑人民币",
        "type": "SINA",
        "api_code": "fx_susdcny", 
        "target_price": 6.8000, 
        "note": "/暂无"
    },
    
    # 可转债平均价格 (虚拟标的)
    "CB/AVG": {
        "name": "可转债平均价格",
        "type": "CB_AVG",
        "api_code": None, # 虚拟标的无需 api_code
        "target_price": 115.00,
        "note": "/暂无"
    }
    # 以后新增标的，只需在此添加一个配置块，无需修改 TARGET_STOCKS, TARGET_PRICES, TARGET_NOTES 等。
}

# =========================================================================

# --- 所有函数（日志、通知、采集、辅助、HTML生成）均保持原样，以确保兼容性 ---
# （因篇幅限制，这里省略了未修改的函数内容，但在实际代码中它们是完整的。）

# ==================== 日志操作和通知函数 (保持不变) ====================
def load_notification_log():
# ... (函数体与原代码一致)
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
# ... (函数体与原代码一致)
    """保存通知日志文件，用于记录通知历史。"""
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=4)
        print(f"成功保存通知日志文件: {NOTIFICATION_LOG_FILE}")
    except IOError as e:
        print(f"错误：无法写入通知日志文件: {e}")

def send_serverchan_notification(title, content):
# ... (函数体与原代码一致)
    """通过 Server酱 发送通知。"""
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

# ==================== 采集函数 (保持不变) ====================
def get_data_sina(stock_api_code):
# ... (函数体与原代码一致)
    """使用新浪财经API获取指定证券的实时价格。"""
    url = f"http://hq.sinajs.cn/list={stock_api_code}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'http://finance.sina.com.cn/'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10) 
        response.encoding = 'gbk'
        data = response.text
        if response.status_code != 200 or '="' not in data:
            return {"error": "获取失败", "detail": f"HTTP状态码: {response.status_code}"}
        data_content = data.split('="')[1].strip('";')
        parts = data_content.split(',')
        if len(parts) < 4:
            # 兼容外汇数据，外汇价格在 parts[3]，前两个是开盘和昨收
            if stock_api_code.startswith('fx_') and len(parts) >= 4 and parts[3].replace('.', '', 1).isdigit():
                return {
                    "current_price": float(parts[3]),
                    "open_price": float(parts[0]) if len(parts) > 0 and parts[0].replace('.', '', 1).isdigit() else None,
                    "prev_close": float(parts[1]) if len(parts) > 1 and parts[1].replace('.', '', 1).isdigit() else None,
                }
            return {"error": "解析失败", "detail": "数据项不足"}
        current_price = parts[3]
        if current_price and current_price.replace('.', '', 1).isdigit():
            return {
                "current_price": float(current_price),
                "open_price": float(parts[1]),
                "prev_close": float(parts[2]),
            }
        else:
            return {"error": "解析失败", "detail": "价格数据无效"}
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}


def get_cb_codes_from_eastmoney():
# ... (函数体与原代码一致)
    """通过东方财富网的公开接口，动态获取所有正在交易中的可转债代码列表。"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=SECURITY_CODE&sortTypes=-1&pageSize=1000&pageNumber=1&reportName=RPT_BOND_CB_LIST&columns=SECURITY_CODE"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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
            if code.startswith('11') or code.startswith('13') or code.startswith('14'):
                sina_code = f"sh{code}"
            elif code.startswith('12'):
                sina_code = f"sz{code}"
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
# ... (函数体与原代码一致)
    """通过新浪 API 批量获取指定可转债列表的价格，并计算有效价格的平均值。"""
    global MAX_CB_PRICE
    if not codes_list:
        return {"error": "计算失败", "detail": "可转债代码列表为空，无法进行计算。"}
    query_string = ",".join(codes_list)
    url = f"http://hq.sinajs.cn/list={query_string}" 
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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
                if len(parts) > 3:
                    price_str = parts[3] 
                    if price_str and price_str.replace('.', '', 1).isdigit():
                        price_float = float(price_str)
                        if price_float > 0 and price_float < MAX_CB_PRICE:
                            prices.append(price_float)
        if not prices:
            return {"error": "计算失败", "detail": f"已获取 {len(codes_list)} 个代码，但新浪未返回有效或低于 {MAX_CB_PRICE:.2f} 的价格数据。"}
        avg_price = sum(prices) / len(prices)
        return {
            "current_price": avg_price,
            "open_price": None, 
            "prev_close": None, 
            "count": len(prices)
        }
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": f"数据处理异常: {str(e)}"}

# ==================== 辅助函数 (保持不变) ====================
def is_trading_time():
# ... (函数体与原代码一致)
    """判断当前时间是否处于中国证券市场的正常交易时段 (北京时间)。"""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()
    if weekday >= 5:
        return False
    am_start = 9 * 60 + 30
    am_end = 11 * 60 + 30
    pm_start = 13 * 60 + 0
    pm_end = 15 * 60 + 0
    current_minutes = hour * 60 + minute
    if (current_minutes >= am_start and current_minutes <= am_end) or \
       (current_minutes >= pm_start and current_minutes <= pm_end):
        return True
    return False

# ==================== HTML 生成函数 (保持不变) ====================
def create_html_content(stock_data_list):
# ... (函数体与原代码一致)
    """生成带有价格表格、目标比例和自动刷新功能的HTML内容。"""
    global MAX_CB_PRICE
    global REFRESH_INTERVAL
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    table_rows = []
    if is_trading_time():
        status_text = '<span style="color: #27ae60;">正常运行</span>'
    else:
        status_text = '非交易时间'
    timestamp_with_status = f"{timestamp} | {status_text}"
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
        target_display = f"{data['target_price']:.4f}"
        price_display = "N/A"
        ratio_display = "N/A"
        note_display = data.get('note', '')
        if data['is_error']:
            price_display = f"数据错误: {data.get('detail', '未知错误')}"
            price_color = '#e74c3c'
        else:
            if data['code'] == 'USD/CNY':
                price_display = f"{data['current_price']:.4f}"
            elif data['code'] == 'CB/AVG':
                price_display = f"{data['current_price']:.3f}"
            else:
                price_display = f"{data['current_price']:.3f}"
            if data['current_price'] >= data['target_price']:
                price_color = '#e67e22'
            else:
                price_color = '#27ae60'
            if data.get('target_ratio') is not None:
                ratio_value = data['target_ratio']
                ratio_display = f"{ratio_value * 100:.2f}%"
                if ratio_value < 0:
                    ratio_color = '#27ae60' 
                elif ratio_value > 0:
                    ratio_color = '#e67e22'
                else:
                    ratio_color = '#3498db'
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
    # --- 2. 完整的 HTML 模板 ---
    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="{REFRESH_INTERVAL}">
    <title>数据展示</title>
    <meta name="robots" content="noindex, nofollow">
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; margin-top: 50px; background-color: #f4f4f9; }}
        h1 {{ color: #2c3e50; font-size: 2.5em; }}
        table {{ 
            width: 95%;
            margin: 30px auto; 
            border-collapse: collapse; 
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: white;
        }}
        th, td {{ 
            border: 1px solid #ddd; 
            padding: 15px; 
            text-align: center;
            font-size: 1.0em;
        }}
        th:last-child, td:last-child {{
            text-align: left;
        }}
        th {{ 
            background-color: #3498db; 
            color: white; 
            font-weight: bold; 
        }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .timestamp {{ color: #7f8c8d; margin-top: 30px; font-size: 1.2em; }}
        .note p {{ color: #34495e; margin: 5px 0; font-size: 1em;}}
    </style>
</head>
<body>
    <h1>数据展示 (按目标比例排序)</h1>
    
    <table>
        {table_content}
    </table>

    <div class="timestamp">数据更新时间: {timestamp_with_status}</div>
    <div class="note">
        <p>📌 **代码运行时间说明**：本代码由 GitHub Actions 在**交易日**的**运行。</p>
        <p>📌 **可转债均价计算说明**：均价计算已**剔除**价格大于或等于 {MAX_CB_PRICE:.2f} 的标的。（相当于暂停该功能）</p>
        <p>注意：本页面每 {REFRESH_INTERVAL // 60} 分钟自动重新加载，以获取最新数据。</p>
    </div>
</body>
</html>
"""
    return html_template


# --- 主逻辑 (已重构以使用 ALL_TARGET_CONFIGS) ---
if __name__ == "__main__":
    
    all_stock_data = []
    cb_avg_data_for_display = None # 用于存储可转债平均价的临时计算结果
    
    # 1. 预先处理需要动态列表的计算型标的 (CB_AVG)
    # 查找 CB_AVG 的配置
    cb_config = next((c for c in ALL_TARGET_CONFIGS.values() if c['type'] == 'CB_AVG'), None)
    
    if cb_config:
        codes_list, cb_error_msg = get_cb_codes_from_eastmoney()
        
        if cb_error_msg:
            cb_avg_data_for_display = {"error": "代码列表获取失败", "detail": cb_error_msg}
        else:
            cb_avg_data_for_display = get_cb_avg_price_from_list(codes_list)
    
    
    # 2. 遍历集中配置，采集数据并组装
    for code, config in ALL_TARGET_CONFIGS.items():
        
        api_data = {}
        
        if config['type'] == 'SINA':
            # SINA 类型：直接调用 API
            api_data = get_data_sina(config["api_code"])
            
        elif config['type'] == 'CB_AVG':
            # CB_AVG 类型：使用预先计算的结果
            api_data = cb_avg_data_for_display
            
        
        is_error = "error" in api_data
        current_price = api_data.get("current_price")
        
        final_data = {
            "name": config["name"],
            "code": code, # 使用 ALL_TARGET_CONFIGS 的 key 作为唯一代码
            "target_price": config["target_price"], # 直接从集中配置中获取
            "note": config["note"],                 # 直接从集中配置中获取
            "is_error": is_error,
            "current_price": current_price,
            **api_data
        }
        
        # 修正可转债平均价格的显示名称
        if config['type'] == 'CB_AVG' and 'count' in api_data and not is_error:
            final_data['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"

        all_stock_data.append(final_data)
        
    # 3. 计算目标比例并排序 (保持不变)
    
    # 计算目标比例 (Target Ratio): (当前价位 - 目标价位) / 当前价位
    for item in all_stock_data:
        item['target_ratio'] = None 
        
        if not item['is_error'] and item['current_price'] is not None and item['current_price'] != 0:
            current_price = item['current_price']
            target_price = item['target_price']
            item['target_ratio'] = (current_price - target_price) / current_price
        
    # 按目标比例升序排序 (从低到高)
    all_stock_data.sort(key=lambda x: x['target_ratio'] if x['target_ratio'] is not None else float('inf'))


    # 4. 目标价位通知 (保持不变)
    
    print("--- 正在检查目标价位通知 ---")
    
    today_date = datetime.now().strftime('%Y-%m-%d')
    notification_log = load_notification_log() 
    log_updated = False 

    for item in all_stock_data:
        code = item.get('code')
        name = item.get('name')
        ratio = item.get('target_ratio')
        
        if item['is_error'] or ratio is None:
            continue
            
        is_triggered = abs(ratio) <= NOTIFICATION_TOLERANCE
        is_notified_today = notification_log.get(code) == today_date

        if is_triggered and not is_notified_today:
            
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
            
            send_success = send_serverchan_notification(title, content)
            
            if send_success:
                notification_log[code] = today_date
                log_updated = True
    
    if log_updated:
        save_notification_log(notification_log)


    # 5. 生成 HTML 文件 (保持不变)
    
    html_content = create_html_content(all_stock_data)

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个证券/指数数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")
