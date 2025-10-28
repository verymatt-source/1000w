import requests
import os
import time # 保留导入，作为未来扩展功能的占位符
import json # 用于解析 API 响应和处理通知日志文件
import re # 用于从新浪 API 批量返回的字符串中提取价格数据
from datetime import datetime
from operator import itemgetter # 用于列表排序操作
import calendar # 用于辅助判断周末/交易日

# --- 全局配置 ---
OUTPUT_FILE = "index_price.html"  # 最终生成的 HTML 报告文件名
REFRESH_INTERVAL = 300  # HTML 页面自动刷新间隔（秒），即 5 分钟
MAX_CB_PRICE = 1000.00 # 可转债平均价计算时，剔除高于或等于此价格的标的

# ======================= 通知配置区域 =======================
NOTIFICATION_TOLERANCE = 0.005  # 触发通知的目标比例（Target Ratio）容忍度（绝对值）
NOTIFICATION_LOG_FILE = "notification_log.json"  # 记录已发送通知历史的文件路径
# =====================================================================

# ======================= 【核心配置区域】所有监控标的配置 =======================

# ALL_TARGET_CONFIGS：集中配置所有监控标的的信息。
# key: 标的内部唯一代码，用于日志和通知
# type: 数据采集方式 ('SINA' 或 'CB_AVG')
# api_code: 实际用于新浪 API 查询的代码
# target_price: 目标价格阈值
# note: 标的备注说明

ALL_TARGET_CONFIGS = {
    # 【新增】上证指数 (内部代码 SSEC)
    "SSEC": {
        "name": "上证指数",
        "type": "SINA",
        "api_code": "sh000001",  # 新浪 API 的上证指数代码
        "target_price": 3000.00, # 【注意】请根据需要修改您的目标价位
        "note": "/暂无"
    },
    
    # 证券公司指数
    "399975": {
        "name": "证券公司指数",
        "type": "SINA", 
        "api_code": "sz399975",
        "target_price": 700.00,  
        "note": "/暂无"         
    }, 
    
    # 美元兑人民币汇率
    "USD/CNY": {
        "name": "美元兑人民币",
        "type": "SINA",
        "api_code": "fx_susdcny", 
        "target_price": 6.8000, 
        "note": "/暂无"
    },
    
    # 可转债平均价格 (计算型虚拟标的)
    "CB/AVG": {
        "name": "可转债平均价格",
        "type": "CB_AVG",
        "api_code": None, # CB_AVG 类型无需新浪代码
        "target_price": 115.00,
        "note": "/暂无"
    }
}

# =========================================================================

# ==================== 日志操作和通知函数 ====================
def load_notification_log():
    """尝试加载通知日志文件，用于检查当日是否已发送通知。"""
    if os.path.exists(NOTIFICATION_LOG_FILE):
        try:
            with open(NOTIFICATION_LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            print("警告：无法读取或解析通知日志文件，将使用新日志。")
            return {}
    return {}

def save_notification_log(log_data):
    """保存通知日志文件，记录通知发送历史。"""
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=4)
        print(f"成功保存通知日志文件: {NOTIFICATION_LOG_FILE}")
    except IOError as e:
        print(f"错误：无法写入通知日志文件: {e}")

def send_serverchan_notification(title, content):
    """通过 Server酱 API 发送通知，需要配置 SERVERCHAN_SCKEY 环境变量。"""
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

# ==================== 采集函数 ====================
def get_data_sina(stock_api_code):
    """使用新浪财经 API 获取单个证券或指数的实时价格。"""
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
            # 兼容外汇数据（价格在 parts[3]）
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
    """通过东方财富 API 动态获取所有正在交易中的可转债代码列表。"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=SECURITY_CODE&sortTypes=-1&pageSize=1000&pageNumber=1&reportName=RPT_BOND_CB_LIST&columns=SECURITY_CODE"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://data.eastmoney.com/kzz/default.html'
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return [], f"HTTP错误：状态码 {response.status_code}"
        data = response.json()
        if data.get('code') != 0:
            return [], f"东方财富API返回错误：{data.get('message', '未知错误')}"
        codes_list = []
        for item in data['result']['data']:
            code = str(item['SECURITY_CODE'])
            if code.startswith('11') or code.startswith('13') or code.startswith('14'):
                sina_code = f"sh{code}" # 沪市可转债代码转换为新浪格式
            elif code.startswith('12'):
                sina_code = f"sz{code}" # 深市可转债代码转换为新浪格式
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
    """通过新浪 API 批量获取可转债价格，并计算有效价格（低于 MAX_CB_PRICE）的平均值。"""
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
                        # 剔除异常高价的可转债
                        if price_float > 0 and price_float < MAX_CB_PRICE:
                            prices.append(price_float)
        if not prices:
            return {"error": "计算失败", "detail": f"已获取 {len(codes_list)} 个代码，但新浪未返回有效或低于 {MAX_CB_PRICE:.2f} 的价格数据。"}
        avg_price = sum(prices) / len(prices)
        return {
            "current_price": avg_price,
            "open_price": None, 
            "prev_close": None, 
            "count": len(prices) # 实际参与计算的标的数量
        }
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": f"数据处理异常: {str(e)}"}

# ==================== 辅助函数 ====================
def is_trading_time():
    """判断当前时间是否处于中国证券市场的正常交易时段（周一至周五 9:30-11:30, 13:00-15:00）。"""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()
    if weekday >= 5: # 周末
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

# ==================== HTML 生成函数 ====================
def create_html_content(stock_data_list):
    """生成包含价格表格、目标比例和自动刷新设置的 HTML 页面内容。"""
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
                price_color = '#e67e22' # 当前价高于目标价时显示橙色
            else:
                price_color = '#27ae60' # 当前价低于目标价时显示绿色
            if data.get('target_ratio') is not None:
                ratio_value = data['target_ratio']
                ratio_display = f"{ratio_value * 100:.2f}%"
                if ratio_value < 0:
                    ratio_color = '#27ae60' # 比例为负（当前价低）时显示绿色
                elif ratio_value > 0:
                    ratio_color = '#e67e22' # 比例为正（当前价高）时显示橙色
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
    # --- 完整的 HTML 模板 ---
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
        <p>📌 **代码运行时间说明**：本代码由 GitHub Actions 在交易时间运行。</p>
        <p>📌 **可转债均价计算说明**：均价已剔除价格大于或等于 {MAX_CB_PRICE:.2f} 的标的。</p>
        <p>注意：本页面每 {REFRESH_INTERVAL // 60} 分钟自动重新加载，以获取最新数据。</p>
    </div>
</body>
</html>
"""
    return html_template


# --- 主逻辑部分 ---
if __name__ == "__main__":
    
    all_stock_data = [] # 存储所有标的最终处理结果的列表
    cb_avg_data_for_display = None # 存储可转债平均价计算的临时结果
    
    # 1. 预处理计算型标的 (CB_AVG)
    
    # 查找 CB_AVG 的配置
    cb_config = next((c for c in ALL_TARGET_CONFIGS.values() if c['type'] == 'CB_AVG'), None)
    
    if cb_config:
        codes_list, cb_error_msg = get_cb_codes_from_eastmoney() # 获取所有可转债代码
        
        if cb_error_msg:
            cb_avg_data_for_display = {"error": "代码列表获取失败", "detail": cb_error_msg}
        else:
            cb_avg_data_for_display = get_cb_avg_price_from_list(codes_list) # 计算平均价
    
    
    # 2. 遍历配置，采集数据并组装
    for code, config in ALL_TARGET_CONFIGS.items():
        
        api_data = {}
        
        if config['type'] == 'SINA':
            # SINA 类型：直接调用新浪 API
            api_data = get_data_sina(config["api_code"])
            
        elif config['type'] == 'CB_AVG':
            # CB_AVG 类型：使用预先计算的结果
            api_data = cb_avg_data_for_display
            
        
        is_error = "error" in api_data
        current_price = api_data.get("current_price")
        
        # 组装最终用于展示和排序的数据结构
        final_data = {
            "name": config["name"],
            "code": code,
            "target_price": config["target_price"],
            "note": config["note"],
            "is_error": is_error,
            "current_price": current_price,
            **api_data
        }
        
        # 修正可转债平均价格的显示名称，添加计算数量
        if config['type'] == 'CB_AVG' and 'count' in api_data and not is_error:
            final_data['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"

        all_stock_data.append(final_data)
        
    # 3. 计算目标比例并排序
    
    # 计算目标比例 (Target Ratio): (当前价位 - 目标价位) / 当前价位
    for item in all_stock_data:
        item['target_ratio'] = None 
        
        if not item['is_error'] and item['current_price'] is not None and item['current_price'] != 0:
            current_price = item['current_price']
            target_price = item['target_price']
            item['target_ratio'] = (current_price - target_price) / current_price
        
    # 按目标比例升序排序 (最小比例排在最前)
    all_stock_data.sort(key=lambda x: x['target_ratio'] if x['target_ratio'] is not None else float('inf'))


    # 4. 目标价位通知逻辑
    
    print("--- 正在检查目标价位通知 ---")
    
    today_date = datetime.now().strftime('%Y-%m-%d')
    notification_log = load_notification_log() # 加载历史通知记录
    log_updated = False 
    
    # =========================================================
    # === 【临时测试代码】验证 notification_log.json 保存功能 ===
    notification_log['TEST_LOG_VALIDATION'] = today_date # 强制添加一个测试记录
    log_updated = True                           # 强制设置更新标志为 True
    # =========================================================
    
    for item in all_stock_data:
        code = item.get('code')
        name = item.get('name')
        ratio = item.get('target_ratio')
        
        if item['is_error'] or ratio is None:
            continue
            
        is_triggered = abs(ratio) <= NOTIFICATION_TOLERANCE # 检查比例是否在容忍度范围内
        is_notified_today = notification_log.get(code) == today_date # 检查当日是否已发送

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
            
            send_success = send_serverchan_notification(title, content) # 发送通知
            
            if send_success:
                notification_log[code] = today_date
                log_updated = True
    
    if log_updated:
        save_notification_log(notification_log) # 保存更新后的日志


    # 5. 生成 HTML 文件
    
    html_content = create_html_content(all_stock_data) # 生成最终的 HTML 报告

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个证券/指数数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")



