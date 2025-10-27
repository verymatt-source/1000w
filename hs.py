import requests
import os
import time
import json # 用于解析东方财富API返回的JSON数据 / 【新增】用于日志文件操作
import re # 用于解析新浪批量API返回的字符串数据
from datetime import datetime
from operator import itemgetter # 用于列表排序

# --- 全局配置 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒
MAX_CB_PRICE = 9999.00 # 可转债计算平均价时可设置剔除价格，暂时不考虑剔除，因集思录、ninwin都没有剔除畸高数据

# ======================= 通知配置区域 =======================
# 用于判断是否达到目标价位的浮点数容忍度。例如 0.0001 表示 0.01% 的容忍范围。
# 触发条件：abs(目标比例) <= NOTIFICATION_TOLERANCE (即 现价 ≈ 目标价)
NOTIFICATION_TOLERANCE = 0.0001 
# 记录已发送通知的日志文件，用于实现每日只发送一次
NOTIFICATION_LOG_FILE = "notification_log.json" 
# =================================================================

# ======================= 集中配置区域 (新增/修改) =======================

# 1. 【新增】集中配置所有标的的【目标价位】
# 键必须与 TARGET_STOCKS 或 CALCULATED_TARGETS 中 config['code'] 的值保持一致。
TARGET_PRICES = {
    "399975": 700.00,  # 证券公司指数
    "USD/CNY": 6.8000, # 美元兑人民币
    "CB/AVG": 115.00   # 可转债平均价格
}

# 2. 【新增】集中配置所有标的的【备注】
# 键必须与 TARGET_STOCKS 或 CALCULATED_TARGETS 中 config['code'] 的值保持一致。
TARGET_NOTES = {
    "399975": "中证证券公司指数，低估买入，高估卖出。",
    "USD/CNY": "长期观察汇率，支撑位和压力位需另行关注。",
    "CB/AVG": "核心仓位指标，反映可转债整体估值水平。"
}


# ======================= 模块化配置 1：新浪 API 数据源 (指数/外汇) (修改) =======================
# 定义需要采集的证券列表。目标价位已移至 TARGET_PRICES。
TARGET_STOCKS = {
    
    "sz399975": {
        "name": "证券公司指数",
        "code": "399975", 
        # "target_price": 700.00 # 已移除，改为引用 TARGET_PRICES
    }, 
    
    # 美元汇率：
    "fx_susdcny": {
        "name": "美元兑人民币",
        "code": "USD/CNY",
        # "target_price": 7.0000 # 已移除，改为引用 TARGET_PRICES
    }
}

# ======================= 模块化配置 2：计算目标配置 (可转债) (修改) =======================
CALCULATED_TARGETS = {
    "cb_avg_price": {
        "name": "可转债平均价格", 
        "code": "CB/AVG", # 虚拟代码，用于显示和在 TARGET_PRICES 中查找配置
        # "target_price": 130.00 # 已移除，改为引用 TARGET_PRICES
    }
}


# ==================== 日志操作和通知函数 (新增) ====================

def load_notification_log():
    """尝试加载通知日志文件。如果文件不存在或解析失败，则返回空字典。"""
    # 注意：在 GitHub Actions 中，文件可能不存在，这是正常情况。
    if os.path.exists(NOTIFICATION_LOG_FILE):
        try:
            with open(NOTIFICATION_LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            print("警告：无法读取或解析通知日志文件，将使用新日志。")
            return {}
    return {}

def save_notification_log(log_data):
    """保存通知日志文件。"""
    try:
        with open(NOTIFICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            # 格式化保存，确保 JSON 文件可读
            json.dump(log_data, f, ensure_ascii=False, indent=4)
        print(f"成功保存通知日志文件: {NOTIFICATION_LOG_FILE}")
    except IOError as e:
        print(f"错误：无法写入通知日志文件: {e}")


def send_serverchan_notification(title, content):
    """
    通过 Server酱 发送通知。
    
    参数:
        title (str): 消息标题。
        content (str): 消息内容，支持 Markdown 格式。
        
    返回:
        bool: 通知是否发送成功。
    """
    # 从环境变量中读取 SCKEY (必须与步骤 2 中设置的名称一致)
    SCKEY = os.environ.get('SERVERCHAN_SCKEY')
    
    if not SCKEY:
        print("警告：未找到 SERVERCHAN_SCKEY 环境变量，通知功能跳过。")
        return False

    # Server酱 Turbo API URL
    url = f"https://sctapi.ftqq.com/{SCKEY}.send"
    
    data = {
        "title": title,
        "desp": content # 使用 desp 字段支持 Markdown 格式
    }
    
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


# ==================== 采集函数 1：新浪 API (单个证券/外汇) (保持不变) ====================
def get_data_sina(stock_api_code):
    """
    使用新浪财经API获取指定证券的实时价格，并返回一个包含多项数据的字典。
    """
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
            return {"error": "数据格式错误", "detail": f"返回数据项不足: {len(parts)}"}

        current_price = float(parts[3])
        
        # 针对外汇和指数返回不同的键值
        if stock_api_code.startswith("fx_"):
            # 外汇数据格式：[0:名称, 1:开盘, 2:昨收, 3:现价, 4:最高, 5:最低, ...]
            # 现价位于 parts[3]
            price = current_price
            
        else:
            # A股指数数据格式：[0:名称, 1:开盘, 2:昨收, 3:现价, 4:最高, 5:最低, ...]
            # 现价位于 parts[3]
            price = current_price
            
        return {
            "current_price": price,
            "open": float(parts[1]),
            "prev_close": float(parts[2]),
            "high": float(parts[4]),
            "low": float(parts[5]),
            "trade_time": parts[30] if len(parts) > 30 else 'N/A' # 交易时间
        }

    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except ValueError:
        return {"error": "价格转换错误", "detail": "API返回的非数字价格"}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}

# ==================== 采集函数 2：可转债代码列表 (东方财富 API) (保持不变) ====================
def get_cb_codes_from_eastmoney():
    """从东方财富API获取所有可转债的代码列表。"""
    
    # 修正后的 API URL
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    
    # 修正后的 API 请求参数
    params = {
        'pn': '1',               # 页码
        'pz': '1000',            # 每页数量，确保一次获取所有
        'fs': 'm:100+t:3,m:100+t:4,m:100+t:1,m:100+t:2', # 筛选条件：已上市可转债，防止获取到未发行的
        'fields': 'f12,f14',     # f12: 代码, f14: 名称
        'fid': 'f3',             # 排序字段
        'ut': 'bd1d9ddb04089700cf9c3f8865899b59', # 统一的 ut 参数
        'fltt': '2',
        'invt': '2',
        'cb:0.01:9999.00:0',     # 避免接口缓存，随机数或时间戳
        '_': int(time.time() * 1000) # 时间戳
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # API 结构不同，数据位于 data['data']['diff']
        if data.get('data') is None or 'diff' not in data['data']:
            return None, f"API返回错误码: {data.get('code', '未知')}"

        codes_list = []
        for item in data['data']['diff']:
            code = item['f12'] # 证券代码
            # 统一为新浪 API 格式：沪市(sh) 或 深市(sz)
            prefix = 'sh' if code.startswith('11') or code.startswith('13') else 'sz'
            codes_list.append(f"{prefix}{code}")
            
        return codes_list, None

    except requests.exceptions.RequestException as e:
        return None, f"网络错误: {e}"
    except Exception as e:
        return None, f"数据处理错误: {e}"

# ==================== 采集函数 3：可转债平均价格 (新浪 API 批量) (保持不变) ====================
def get_cb_avg_price_from_list(codes_list):
    """
    根据可转债代码列表，批量获取价格并计算平均价。
    
    参数:
        codes_list (list): 包含 'shXXXXXX' 或 'szXXXXXX' 格式代码的列表。
        
    返回:
        dict: 包含 'current_price' (平均价), 'count' (数量) 或 'error' 的字典。
    """
    if not codes_list:
        return {"error": "代码列表为空", "detail": "未获取到任何可转债代码"}

    api_codes_str = ",".join(codes_list)
    url = f"http://hq.sinajs.cn/list={api_codes_str}"
    
    try:
        response = requests.get(url, timeout=15)
        response.encoding = 'gbk'
        data = response.text
        
        if response.status_code != 200:
            return {"error": "批量获取失败", "detail": f"HTTP状态码: {response.status_code}"}
        
        total_price = 0.0
        valid_count = 0
        
        # 使用正则表达式分割每个证券的数据
        # var hq_str_sz123001="... , 价格, ..."
        pattern = re.compile(r'var\s+hq_str_.*?="([^"]+)"')
        matches = pattern.findall(data)
        
        for match in matches:
            parts = match.split(',')
            
            # 价格位于第4个字段 (parts[3])
            # A股/可转债格式：[0:名称, 1:开盘, 2:昨收, 3:现价, 4:最高, 5:最低, ...]
            if len(parts) >= 4 and parts[3].replace('.', '', 1).isdigit():
                price = float(parts[3])
                
                # 剔除价格过高的标的（例如停牌、转股或错误数据）
                if price < MAX_CB_PRICE and price > 0:
                    total_price += price
                    valid_count += 1
            
        if valid_count > 0:
            avg_price = total_price / valid_count
            return {
                "current_price": avg_price,
                "count": valid_count,
                "high": max([float(p.split(',')[4]) for p in matches if len(p.split(',')) >= 5 and p.split(',')[4].replace('.', '', 1).isdigit()], default=0),
                "low": min([float(p.split(',')[5]) for p in matches if len(p.split(',')) >= 6 and p.split(',')[5].replace('.', '', 1).isdigit()], default=0)
            }
        else:
            return {"error": "计算失败", "detail": "未获取到有效的可转债价格数据"}

    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}


# ==================== HTML 页面生成函数 (保持不变) ====================
def generate_html(all_stock_data):
    """根据数据生成最终的 HTML 页面内容。"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    table_rows = []

    # --- 1. 生成表格内容 ---
    for data in all_stock_data:
        # 默认值
        price_display = "N/A"
        price_color = '#7f8c8d' # 灰色
        ratio_display = "N/A"
        ratio_color = '#7f8c8d' 
        target_display = f"{data.get('target_price', 'N/A'):.4f}" if data.get('target_price') is not None else "N/A"
        note_display = data.get('note', '无')

        # 错误处理
        if data.get('is_error'):
            price_display = f"错误: {data['detail']}"
            
        # 成功处理
        elif data.get('current_price') is not None:
            price_value = data['current_price']
            price_display = f"{price_value:.4f}"
            
            # 价格颜色：高于目标价位红色；低于目标价位绿色
            target_price = data.get('target_price')
            if target_price is not None:
                if price_value > target_price:
                    price_color = '#e74c3c' # 红色
                elif price_value < target_price:
                    price_color = '#27ae60' # 绿色
                else:
                    price_color = '#3498db' # 蓝色

            # 目标比例显示
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

    # --- 2. 完整的 HTML 模板 ---
    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="{REFRESH_INTERVAL}">
    <title>证券指数实时监控</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #ecf0f1; color: #34495e; padding: 20px; }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #bdc3c7; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); background-color: #fff; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: center; }}
        th {{ background-color: #3498db; color: white; text-transform: uppercase; }}
        td:nth-child(1) {{ text-align: left; font-weight: bold; }}
        tr:hover {{ background-color: #f5f5f5; }}
        .header-ratio {{ width: 15%; }}
        .header-target {{ width: 15%; }}
        .header-price {{ width: 15%; color: white; font-weight: bold; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .timestamp {{ color: #7f8c8d; margin-top: 30px; font-size: 1.2em; }}
        .note {{ color: #e74c3c; margin-top: 10px; }}
    </style>
</head>
<body>
    <h1>证券指数实时监控</h1>
    
    <table>
        <thead>
            <tr>
                <th>标的名称</th>
                <th>代码</th>
                <th class="header-target">目标价位</th>
                <th class="header-price">当前价位</th>
                <th class="header-ratio">偏离目标比例</th>
                <th style="width: 30%; text-align: left;">备注</th>
            </tr>
        </thead>
        <tbody>
            {table_content}
        </tbody>
    </table>

    <div class="timestamp">更新时间: {timestamp}</div>
    <div class="note">注意：此页面每 {REFRESH_INTERVAL // 60} 分钟自动重新加载，以获取最新数据。</div>
</body>
</html>
"""
    return html_template

# --- 主逻辑 ---
if __name__ == "__main__":
    
    all_stock_data = []
    
    # ================= 运行模块 1：新浪 API 数据采集 =================
    
    # 【模块化运行】：遍历配置中的所有证券
    for api_code, config in TARGET_STOCKS.items():
        
        # 1. 尝试获取 API 数据 (返回字典)
        api_data = get_data_sina(api_code)
        
        # 2. 从集中配置中获取目标价和备注
        target_code = config["code"]
        target_price = TARGET_PRICES.get(target_code)
        target_note = TARGET_NOTES.get(target_code, '无')
        
        # 3. 合并配置数据和 API 数据
        final_data = {
            "name": config["name"],
            "code": target_code,
            "target_price": target_price,
            "note": target_note,
            "is_error": "error" in api_data,
            "current_price": api_data.get("current_price"), # 确保 current_price 字段存在
            **api_data
        }
        all_stock_data.append(final_data)
        
    # ================= 运行模块 2：可转债平均价格计算 (动态列表) =================
    
    # Step 2.1: 动态获取最新的可转债代码列表 (东方财富网)
    codes_list, error_msg = get_cb_codes_from_eastmoney()
    
    # Step 2.2: 根据列表结果，决定是报错还是计算平均价格
    config = CALCULATED_TARGETS['cb_avg_price']
    
    if error_msg:
        # 如果获取代码列表失败，直接记录错误
        api_data = {"error": "代码列表获取失败", "detail": error_msg}
    else:
        # 如果代码列表获取成功，调用新浪 API 批量计算平均价格
        api_data = get_cb_avg_price_from_list(codes_list)
    
    # 从集中配置中获取目标价和备注
    target_code = config["code"]
    target_price = TARGET_PRICES.get(target_code)
    target_note = TARGET_NOTES.get(target_code, '无')
    
    final_data = {
        "name": config["name"],
        "code": target_code,
        "target_price": target_price, # 引用集中配置的目标价
        "note": target_note,         # 引用集中配置的备注
        "is_error": "error" in api_data,
        "current_price": api_data.get("current_price"),
        **api_data
    }
    
    # 动态更新名称，以显示当前计算了多少个可转债 (增强信息展示)
    if 'count' in api_data and not final_data['is_error']:
        final_data['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"
    else:
        final_data['name'] = config['name'] # 保持默认名称
        
    all_stock_data.append(final_data)
        
    # ================= 运行模块 3：计算目标比例并排序 =================
    
    # 1. 计算目标比例 (Target Ratio): (当前价位 - 目标价位) / 当前价位
    for item in all_stock_data:
        # 初始化比例为 None，用于错误或无效数据
        item['target_ratio'] = None 
        
        if not item['is_error'] and item['current_price'] is not None and item['current_price'] != 0:
            current_price = item['current_price']
            target_price = item['target_price']
            
            # 确保目标价位已配置
            if target_price is not None:
                # 计算目标比例
                item['target_ratio'] = (current_price - target_price) / current_price
        
    # 2. 按目标比例升序排序 (从低到高)
    # 排序键：使用 lambda 表达式。如果 target_ratio 为 None (数据错误/缺失)，
    # 则将其视为一个非常大的数 (float('inf'))，排在最后。
    all_stock_data.sort(key=lambda x: x.get('target_ratio') if x.get('target_ratio') is not None else float('inf'))
    
    
    # ================= 运行模块 4：目标价位通知 (已修改) =================
    
    # ----------------------------------------------------
    # 【通知判断和发送】: 遍历所有数据，检查是否需要发送通知
    # ----------------------------------------------------
    today_date = datetime.now().strftime('%Y-%m-%d')
    notification_log = load_notification_log() 
    log_updated = False # 标记日志是否被修改

    for item in all_stock_data:
        # 1. 变量初始化和准备
        code = item.get('code')
        name = item.get('name')
        ratio = item.get('target_ratio')
        
        # 确保数据有效且配置了目标价位
        if item['is_error'] or ratio is None:
            continue
            
        # 2. 核心判断逻辑
        # 检查是否达到容忍度范围
        is_triggered = abs(ratio) <= NOTIFICATION_TOLERANCE
        # 检查今天是否已经发送过通知
        is_notified_today = notification_log.get(code) == today_date

        if is_triggered and not is_notified_today:
            
            # 构造通知内容
            # 【修改点 1: 新标题格式】
            title = f"【{name}】到达目标价位！！！" 
            
            # 【修改点 2: 新内容格式 - 使用 Markdown 表格，更清晰】
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
                
    # 4. 保存日志文件
    if log_updated:
        save_notification_log(notification_log)


    # ================= 运行模块 5：生成 HTML 文件 =================
    
    html_content = generate_html(all_stock_data)

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"HTML 文件 {OUTPUT_FILE} 已成功生成。")
    except Exception as e:
        print(f"写入 HTML 文件失败: {e}")
