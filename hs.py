import requests
import os
import time
import json # 新增：用于解析东方财富API返回的JSON数据
import re # 新增：用于解析新浪批量API返回的字符串数据
from datetime import datetime

# --- 配置 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒

# ======================= 模块化配置 1：新浪 API 数据源 (指数/外汇) =======================
# 定义需要采集的证券列表和自定义的目标价位。键是新浪API的股票代码格式。
TARGET_STOCKS = {
    
    "sz399975": {
        "name": "证券公司指数",
        "code": "399975",
        "target_price": 700.00  # 您的预设目标价
    }, 
    
    # 美元汇率：
    "fx_susdcny": {
        "name": "美元兑人民币",
        "code": "USD/CNY",
        "target_price": 7.0000  # 您的预设目标价（例如 7.00）
    }
}

# ======================= 模块化配置 2：计算目标配置 (可转债) (新增) =======================
CALCULATED_TARGETS = {
    "cb_avg_price": {
        "name": "可转债平均价格", 
        "code": "CB/AVG", # 虚拟代码，用于显示
        "target_price": 130.00 # 您的预设目标价
    }
}


# ==================== 采集函数 1：新浪 API (单个证券/外汇) ====================
def get_data_sina(stock_api_code):
    """
    使用新浪财经API获取指定证券的实时价格，并返回一个包含多项数据的字典。
    (此函数沿用原有逻辑，略作通用化修改)
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
        
        # 检查响应状态和数据格式
        if response.status_code != 200 or '="' not in data:
            return {"error": "获取失败", "detail": f"HTTP状态码: {response.status_code}"}

        # 新浪数据格式：v_sz399975="指数名称,今开,昨收,当前价,最高,最低..."
        data_content = data.split('="')[1].strip('";')
        parts = data_content.split(',')
        
        if len(parts) < 4:
            return {"error": "解析失败", "detail": "数据项不足"}
            
        current_price = parts[3]
        
        # 验证价格数据的有效性
        if current_price and current_price.replace('.', '', 1).isdigit():
            return {
                "current_price": float(current_price),
                "open_price": float(parts[1]),  # 今开
                "prev_close": float(parts[2]),  # 昨收
            }
        else:
            return {"error": "解析失败", "detail": "价格数据无效"}
            
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}


# ==================== 采集函数 2.1：动态代码获取 (东方财富) (新增模块，与原有逻辑隔离) ====================
def get_cb_codes_from_eastmoney():
    """
    通过爬取东方财富网的公开接口，动态获取所有正在交易中的可转债代码列表。
    返回格式: ['sh11xxxx', 'sz12xxxx', ...]
    """
    # 东方财富网可转债列表接口 (一次性获取全部数据，pageSize=1000)
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=SECURITY_CODE&sortTypes=-1&pageSize=1000&pageNumber=1&reportName=RPT_BOND_CB_LIST&columns=SECURITY_CODE"
    
    headers = {
        # 【关键】模拟浏览器访问的 Headers，以确保API访问成功
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://data.eastmoney.com/kzz/default.html'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return [], f"HTTP错误：状态码 {response.status_code}"
            
        # 尝试解析 JSON 数据
        data = response.json()
        
        if data.get('code') != 0:
            return [], f"东方财富API返回错误：{data.get('message', '未知错误')}"
            
        codes_list = []
        # 遍历所有数据项，提取代码并加上交易所前缀 (新浪API需要前缀)
        for item in data['result']['data']:
            code = str(item['SECURITY_CODE']) # 确保代码是字符串
            
            # 交易所前缀判断：沪市可转债以 11/13/14 开头，深市以 12 开头
            if code.startswith('11') or code.startswith('13') or code.startswith('14'):
                sina_code = f"sh{code}"
            elif code.startswith('12'):
                sina_code = f"sz{code}"
            else:
                continue # 排除其他不确定的代码
                
            codes_list.append(sina_code)
            
        return codes_list, None # 返回代码列表和None (无错误)
        
    except requests.exceptions.RequestException as e:
        return [], f"网络错误：{str(e)}"
    except json.JSONDecodeError:
        return [], "数据解析失败：返回内容不是有效的 JSON"
    except Exception as e:
        return [], f"未知错误：{str(e)}"


# ==================== 采集函数 2.2：计算平均价格 (新浪批量查询) (新增模块) ====================
def get_cb_avg_price_from_list(codes_list):
    """
    通过新浪 API 批量获取指定可转债列表的价格，并计算有效价格的平均值。
    """
    if not codes_list:
        return {"error": "计算失败", "detail": "可转债代码列表为空，无法进行计算。"}

    # 构造批量查询 URL，例如 list=sh11xxxx,sz12xxxx,...
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
        
        # 1. 解析所有可转债数据
        # 使用正则表达式匹配引号中的所有数据内容，并按行分割
        valid_lines = [line for line in data.split('\n') if line.startswith('var hq_str_')]
        
        prices = []
        
        for line in valid_lines:
            match = re.search(r'="(.+?)"', line)
            if match:
                parts = match.group(1).split(',')
                # 可转债的实时价格位于第4个位置 (parts[3])
                if len(parts) > 3:
                    price_str = parts[3] 
                    # 价格有效性检查：是数字且大于0 (排除停牌或异常数据)
                    if price_str and price_str.replace('.', '', 1).isdigit() and float(price_str) > 0:
                        prices.append(float(price_str))
        
        if not prices:
            # 如果成功获取代码列表，但价格数据为空，可能是非交易时间或接口限制
            return {"error": "计算失败", "detail": f"已获取 {len(codes_list)} 个代码，但新浪未返回有效价格数据。"}

        # 2. 计算平均价格
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
        return {"error": "未知错误", "detail": str(e)}


# ==================== HTML 生成函数 (表格化) ====================
def create_html_content(stock_data_list):
    """
    生成带有价格表格和自动刷新功能的HTML内容。
    (此函数已升级为支持多行表格输出)
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    table_rows = []
    
    table_rows.append("""
        <tr>
            <th>标的名称</th>
            <th>证券代码</th>
            <th>当前价位</th>
            <th>目标价位</th>
        </tr>
    """)
    
    for data in stock_data_list:
        
        # 确定价格的显示颜色
        price_color = '#27ae60'  # 默认绿色
        target_display = f"{data['target_price']:.2f}"
        price_display = "N/A"
        
        if data['is_error']:
            # 错误信息显示为红色
            price_display = f"数据错误: {data.get('detail', '未知错误')}"
            price_color = '#e74c3c'
        else:
            # 根据代码类型确定价格显示格式
            if data['code'] == 'USD/CNY':
                price_display = f"{data['current_price']:.4f}" # 汇率保留四位
            elif data['code'] == 'CB/AVG':
                price_display = f"{data['current_price']:.3f}" # 平均价保留三位
            else:
                price_display = f"{data['current_price']:.3f}"
                
            # 判断是否达到目标价
            if data['current_price'] >= data['target_price']:
                price_color = '#e67e22' # 橙色
            
        # 生成表格行
        row = f"""
        <tr>
            <td>{data['name']}</td>
            <td>{data['code']}</td>
            <td style="color: {price_color}; font-weight: bold;">{price_display}</td>
            <td>{target_display}</td>
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
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; margin-top: 50px; background-color: #f4f4f9; }}
        h1 {{ color: #2c3e50; font-size: 2.5em; }}
        table {{ 
            width: 80%; 
            margin: 30px auto; 
            border-collapse: collapse; 
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            background-color: white;
        }}
        th, td {{ 
            border: 1px solid #ddd; 
            padding: 15px; 
            text-align: center;
            font-size: 1.1em;
        }}
        th {{ 
            background-color: #3498db; 
            color: white; 
            font-weight: bold; 
        }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .timestamp {{ color: #7f8c8d; margin-top: 30px; font-size: 1.2em; }}
        .note {{ color: #e74c3c; margin-top: 10px; }}
    </style>
</head>
<body>
    <h1>证券指数实时监控</h1>
    
    <table>
        {table_content}
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
    
    # ================= 运行模块 1：新浪 API (指数/外汇) =================
    # 遍历固定的证券和外汇配置
    for api_code, config in TARGET_STOCKS.items():
        api_data = get_data_sina(api_code)
        final_data = {
            "name": config["name"],
            "code": config["code"],
            "target_price": config["target_price"],
            "is_error": "error" in api_data,
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
    
    final_data = {
        "name": config["name"],
        "code": config["code"],
        "target_price": config["target_price"],
        "is_error": "error" in api_data,
        **api_data
    }
    
    # 动态更新名称，以显示当前计算了多少个可转债 (增强信息展示)
    if 'count' in api_data and not final_data['is_error']:
        final_data['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"
    else:
        final_data['name'] = config['name'] # 保持默认名称
        
    all_stock_data.append(final_data)
        
    # 3. 生成 HTML 内容
    html_content = create_html_content(all_stock_data)

    # 4. 写入文件
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个证券/指数数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")
