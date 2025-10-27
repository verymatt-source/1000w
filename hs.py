import requests
import os
import time
from datetime import datetime

# --- 配置 ---
OUTPUT_FILE = "index_price.html"
REFRESH_INTERVAL = 1800  # 自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒

# ======================= 模块化配置 1：新浪 API 数据源 =======================
# 定义需要采集的证券/外汇列表和自定义的目标价位
TARGET_STOCKS = {
    # 键是新浪API的代码格式，值是配置信息
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

# ======================= 模块化配置 2：集思录 API 数据源 =======================
# 定义需要采集的集思录指数列表和自定义的目标价位
JISILU_TARGETS = {
    # 键是用于识别的自定义ID，值是配置信息
    "cb_average_price": {
        "name": "可转债平均价格",
        "code": "JSL/CB",
        "target_price": 130.00  # 您的预设目标价
    }
}


# ==================== 采集函数 1：新浪 API (证券/外汇) ====================
def get_data_sina(stock_api_code):
    """
    使用新浪财经API获取指定证券或外汇的实时价格。
    """
    url = f"http://hq.sinajs.cn/list={stock_api_code}"
    # 模拟浏览器请求
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

        # 数据解析...
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


# ==================== 采集函数 2：集思录 API (可转债平均价格) (再次修正 Headers) ====================
def get_data_jisilu():
    """
    使用集思录API获取可转债指数数据，提取平均价格。
    """
    url = "https://www.jisilu.cn/webapi/cb/index_data/"
    
    # 【第二次修正】强化 Headers，增加 Host, Accept 和 Cookie，以更像一个真实的浏览器请求
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.jisilu.cn/web/data/cb/index', 
        'Host': 'www.jisilu.cn', # 明确指定 Host
        'Accept': 'application/json, text/plain, */*', # 明确接受 JSON
        'Cookie': 'JSLCODE=1', # 增加一个默认 Cookie 占位符
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            # 如果 HTTP 状态码不是 200，先抛出错误信息
            return {"error": "获取失败", "detail": f"HTTP状态码: {response.status_code}"}
            
        # 尝试解析 JSON
        data = response.json()
        
        if 'data' not in data:
            return {"error": "解析失败", "detail": "JSON中未找到 'data' 字段"}
        
        # 提取平均价格
        average_price = data['data']['index_data'][0]['cb_average_price']
        
        if average_price:
            return {
                "current_price": float(average_price),
                "open_price": None, 
                "prev_close": None, 
            }
        else:
            return {"error": "解析失败", "detail": "未找到平均价格数据"}
            
    except requests.exceptions.JSONDecodeError as e:
        # 捕获非JSON返回，即 API 拒绝请求
        detail = f"集思录 API 拒绝请求。请检查 Referer 和 Headers 设置是否完整。原始错误：{e}"
        return {"error": "数据错误", "detail": detail}
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}


# ==================== HTML 生成函数 (表格化) ====================
def create_html_content(stock_data_list):
    """
    生成带有价格表格和自动刷新功能的HTML内容。
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
        
        price_color = '#27ae60'  # 默认绿色
        if data['is_error']:
            price_display = f"数据错误: {data['detail']}"
            price_color = '#e74c3c'
        else:
            # 根据数据代码确定小数点位数
            if data['code'] == 'USD/CNY':
                price_display = f"{data['current_price']:.4f}" # 汇率保留四位小数
            else:
                price_display = f"{data['current_price']:.3f}" # 指数/可转债保留三位小数
                
            if data['current_price'] >= data['target_price']:
                price_color = '#e67e22' # 橙色
            
        row = f"""
        <tr>
            <td>{data['name']}</td>
            <td>{data['code']}</td>
            <td style="color: {price_color}; font-weight: bold;">{price_display}</td>
            <td>{data['target_price']:.2f}</td>
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
    
    # ================= 运行模块 1：新浪 API =================
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
        
    # ================= 运行模块 2：集思录 API =================
    for index_id, config in JISILU_TARGETS.items():
        api_data = get_data_jisilu()
        final_data = {
            "name": config["name"],
            "code": config["code"],
            "target_price": config["target_price"],
            "is_error": "error" in api_data,
            **api_data
        }
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
