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

# ======================= 通知配置区域 (新增) =======================
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


# ==================== 采集函数 2.1：动态代码获取 (东方财富) (保持不变) ====================
def get_cb_codes_from_eastmoney():
    """
    通过爬取东方财富网的公开接口，动态获取所有正在交易中的可转债代码列表。
    """
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
            
            # 交易所前缀判断：沪市可转债以 11/13/14 开头，深市以 12 开头
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


# ==================== 采集函数 2.2：计算平均价格 (包含剔除逻辑) (保持不变) ====================
def get_cb_avg_price_from_list(codes_list):
    """
    通过新浪 API 批量获取指定可转债列表的价格，并计算有效价格的平均值。
    剔除价格 >= MAX_CB_PRICE 的标的。
    """
    global MAX_CB_PRICE # 确保引用全局变量
    
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


# ==================== HTML 生成函数 (包含目标比例列和备注) (保持不变) ====================
def create_html_content(stock_data_list):
    """
    生成带有价格表格、目标比例和自动刷新功能的HTML内容。
    """
    global MAX_CB_PRICE
    global REFRESH_INTERVAL
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    table_rows = []
    
    # 【修改】：增加 '备注' 这一列
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
        target_display = f"{data['target_price']:.2f}"
        price_display = "N/A"
        ratio_display = "N/A"
        note_display = data.get('note', '') # 获取备注信息
        
        if data['is_error']:
            # 错误信息显示为红色
            price_display = f"数据错误: {data.get('detail', '未知错误')}"
            price_color = '#e74c3c'
        else:
            # 1. 价格格式化
            if data['code'] == 'USD/CNY':
                price_display = f"{data['current_price']:.4f}"
            elif data['code'] == 'CB/AVG':
                price_display = f"{data['current_price']:.3f}"
            else:
                price_display = f"{data['current_price']:.3f}"
                
            # 2. 当前价位颜色判断 (高于目标价时标橙色)
            if data['current_price'] >= data['target_price']:
                price_color = '#e67e22' # 橙色
            else:
                price_color = '#27ae60' # 绿色

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
            width: 95%; /* 增加表格宽度以容纳备注 */
            margin: 30px auto;
