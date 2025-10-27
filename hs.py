import requests
import os
import time
import json # 用于解析东方财富API返回的JSON数据
import re # 用于解析新浪批量API返回的字符串数据
from datetime import datetime
from operator import itemgetter # 【新增】：用于列表排序

# --- 配置 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒
MAX_CB_PRICE = 1000.00 # 【新增配置】：可转债计算平均价时，剔除价格 >= 500.00 的标的

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

# ======================= 模块化配置 2：计算目标配置 (可转债) =======================
CALCULATED_TARGETS = {
    "cb_avg_price": {
        "name": "可转债平均价格", 
        "code": "CB/AVG", # 虚拟代码，用于显示
        "target_price": 120.00 # 您的预设目标价
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


# ==================== 采集函数 2.1：动态代码获取 (东方财富) ====================
def get_cb_codes_from_eastmoney():
    """
    通过爬取东方财富网的公开接口，动态获取所有正在交易中的可转债代码列表。
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=SECURITY_CODE&sortTypes=-1&pageSize=1000&pageNumber=1&reportName=RPT_BOND_CB_LIST&columns=SECURITY_CODE"
    
    headers = {
        # 模拟浏览器访问的 Headers
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


# ==================== 采集函数 2.2：计算平均价格 (新浪批量查询，包含剔除逻辑) ====================
def get_cb_avg_price_from_list(codes_list):
    """
    通过新浪 API 批量获取指定可转债列表的价格，并计算有效价格的平均值。
    【新增】：剔除价格 >= MAX_CB_PRICE 的标的。
    """
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
        
        # 1. 解析所有可转债数据
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
                        
                        # 【剔除逻辑】：只纳入价格大于0且低于 MAX_CB_PRICE 的标的进行计算
                        if price_float > 0 and price_float < MAX_CB_PRICE:
                            prices.append(price_float)
        
        if not prices:
            return {"error": "计算失败", "detail": f"已获取 {len(codes_list)} 个代码，但新浪未返回有效或低于 {MAX_CB_PRICE:.2f} 的价格数据。"}

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
        return {"error": "未知错误", "detail": f"数据处理异常: {str(e)}"}


# ==================== HTML 生成函数 (包含目标比例列和备注) ====================
def create_html_content(stock_data_list):
    """
    生成带有价格表格、目标比例和自动刷新功能的HTML内容。
    【修改】：增加 '目标比例' 列，更新备注信息。
    """
    # 备注信息中需要用到 MAX_CB_PRICE，直接使用全局常量
    global MAX_CB_PRICE
    global REFRESH_INTERVAL
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    table_rows = []
    
    # 【修改】：增加 '目标比例' 这一列，并调整列顺序
    table_rows.append("""
        <tr>
            <th>标的名称</th>
            <th>证券代码</th>
            <th>目标价位</th>
            <th>当前价位</th>
            <th>目标比例</th> 
        </tr>
    """)
    
    for data in stock_data_list:
        
        price_color = '#27ae60'  # 默认绿色
        ratio_color = '#7f8c8d' # 默认比例颜色
        target_display = f"{data['target_price']:.2f}"
        price_display = "N/A"
        ratio_display = "N/A"
        
        if data['is_error']:
            # 错误信息显示为红色
            price_display = f"数据错误: {data.get('detail', '未知错误')}"
            price_color = '#e74c3c'
        else:
            # 1. 价格格式化
            if data['code'] == 'USD/CNY':
                price_display = f"{data['current_price']:.4f}" # 汇率保留四位
            elif data['code'] == 'CB/AVG':
                price_display = f"{data['current_price']:.3f}" # 平均价保留三位
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
        </tr>
        """
        table_rows.append(row)

    table_content = "".join(table_rows)

    # --- 2. 完整的 HTML 模板 ---
    # 【新增】：在 .note 区域添加运行说明和剔除说明
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
        .note p {{ color: #34495e; margin: 5px 0; font-size: 1em;}}
    </style>
</head>
<body>
    <h1>证券指数实时监控 (按目标比例排序)</h1>
    
    <table>
        {table_content}
    </table>

    <div class="timestamp">数据更新时间: {timestamp}</div>
    <div class="note">
        <p>📌 **代码运行时间说明**：本代码由 GitHub Actions 在**交易日**的**北京时间 09:05 至 16:00** 之间运行。</p>
        <p>📌 **可转债计算说明**：可转债平均价格的计算已**剔除**价格大于或等于 {MAX_CB_PRICE:.2f} 的标的，以排除畸高价格的影响。</p>
        <p>注意：本页面每 {REFRESH_INTERVAL // 60} 分钟自动重新加载，以获取最新数据。</p>
    </div>
</body>
</html>
"""
    return html_template

# --- 主逻辑 ---
if __name__ == "__main__":
    
    all_stock_data = []
    
    # ================= 运行模块 1：新浪 API (指数/外汇) =================
    # 遍历固定的证券和外汇配置，收集初始数据
    for api_code, config in TARGET_STOCKS.items():
        api_data = get_data_sina(api_code)
        final_data = {
            "name": config["name"],
            "code": config["code"],
            "target_price": config["target_price"],
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
    
    final_data = {
        "name": config["name"],
        "code": config["code"],
        "target_price": config["target_price"],
        "is_error": "error" in api_data,
        "current_price": api_data.get("current_price"), # 确保 current_price 字段存在
        **api_data
    }
    
    # 动态更新名称，以显示当前计算了多少个可转债 (增强信息展示)
    if 'count' in api_data and not final_data['is_error']:
        final_data['name'] = f"可转债平均价格 (基于{api_data['count']}个代码计算)"
    else:
        final_data['name'] = config['name'] # 保持默认名称
        
    all_stock_data.append(final_data)
        
    # ================= 运行模块 3：计算目标比例并排序 (新增模块) =================
    
    # 1. 计算目标比例 (Target Ratio): (当前价位 - 目标价位) / 当前价位
    for item in all_stock_data:
        # 初始化比例为 None，用于错误或无效数据
        item['target_ratio'] = None 
        
        if not item['is_error'] and item['current_price'] is not None and item['current_price'] != 0:
            current_price = item['current_price']
            target_price = item['target_price']
            
            # 计算目标比例
            item['target_ratio'] = (current_price - target_price) / current_price
        
    # 2. 按目标比例升序排序 (从低到高)
    # 排序键：使用 lambda 表达式。如果 target_ratio 为 None (数据错误/缺失)，
    # 则返回 float('inf')，确保这些数据排在列表的最后。
    all_stock_data.sort(key=lambda x: x['target_ratio'] if x['target_ratio'] is not None else float('inf'))


    # 3. 生成 HTML 内容
    html_content = create_html_content(all_stock_data)

    # 4. 写入文件
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个证券/指数数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")

