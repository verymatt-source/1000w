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
    # 键是新浪API的股票代码格式，值是用户自定义的目标价位
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

# ======================= 模块化配置 2：集思录 API 数据源 (新增) =======================
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
    使用新浪财经API获取指定证券或外汇的实时价格，并返回一个包含多项数据的字典。
    """
    url = f"http://hq.sinajs.cn/list={stock_api_code}"
    # 模拟浏览器请求
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'http://finance.sina.com.cn/'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10) 
        # 新浪API数据通常是 gbk 编码
        response.encoding = 'gbk'
        data = response.text
        
        if response.status_code != 200 or '="' not in data:
            return {"error": "获取失败", "detail": f"HTTP状态码: {response.status_code}"}

        # 数据格式：v_sz399975="指数名称,今开,昨收,当前价,最高,最低..."
        data_content = data.split('="')[1].strip('";')
        parts = data_content.split(',')
        
        # 对于指数和外汇，当前价都在第4个位置 (parts[3])
        if len(parts) < 4:
            return {"error": "解析失败", "detail": "数据项不足"}
            
        current_price = parts[3]
        
        if current_price and current_price.replace('.', '', 1).isdigit():
            # 成功解析数据
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


# ==================== 采集函数 2：集思录 API (可转债平均价格) (修正 Referer) ====================
def get_data_jisilu():
    """
    使用集思录API获取可转债指数数据，提取平均价格。
    """
    # 这是集思录可转债指数的公开JSON API接口
    url = "https://www.jisilu.cn/webapi/cb/index_data/"
    # 【关键修正】必须添加正确的 Referer 才能成功获取数据
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.jisilu.cn/web/data/cb/index' # 从可转债指数页面发出请求
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        # 集思录数据是JSON格式，Python可以直接解析
        data = response.json()
        
        if response.status_code != 200 or 'data' not in data:
            return {"error": "获取失败", "detail": f"HTTP状态码: {response.status_code}"}
        
        # 【关键解析】集思录数据结构：data['data']['index_data'][0]['cb_average_price']
        average_price = data['data']['index_data'][0]['cb_average_price']
        
        if average_price:
            # 成功解析数据
            return {
                "current_price": float(average_price),
                "open_price": None, 
                "prev_close": None, 
            }
        else:
            return {"error": "解析失败", "detail": "未找到平均价格数据"}
            
    except requests.exceptions.JSONDecodeError as e:
        # 【错误捕捉】捕获非JSON返回，即您之前遇到的错误
        detail = f"Expecting value: line 1 column 1 (char 0)。原因可能是 Referer 不正确或 API 拒绝请求。原始错误：{e}"
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
    # 获取时间戳 (TZ环境变量已在YML中设置，此处获取的是北京时间)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    
    # --- 1. 生成表格的 HTML 内容 ---
    table_rows = []
    
    # 添加表头
    table_rows.append("""
        <tr>
            <th>标的名称</th>
            <th>证券代码</th>
            <th>当前价位</th>
            <th>目标价位</th>
        </tr>
    """)
    
    # 循环添加每一只证券的数据行
    for data in stock_data_list:
        
        # 确定价格的显示颜色
        price_color = '#27ae60'  # 默认绿色
        if data['is_error']:
            # 错误信息显示红色
            price_display = f"数据错误: {data['detail']}"
            price_color = '#e74c3c'
        else:
            # 优化：根据数据代码确定小数点位数
            if data['code'] == 'USD/CNY':
                price_display = f"{data['current_price']:.4f}" # 汇率保留四位小数
            else:
                price_display = f"{data['current_price']:.3f}" # 指数/可转债保留三位小数
                
            if data['current_price'] >= data['target_price']:
                price_color = '#e67e22' # 橙色
            
        # 生成表格行
        row = f"""
        <tr>
            <td>{data['name']}</td>
            <td>{data['code']}</td>
            <td style="color: {price_color}; font-weight: bold;">{price_display}</td>
            <td>{data['target_price']:.2f}</td>
        </tr>
        """
        table_rows.append(row)

    # 将所有行合并成完整的表格
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
    
    # ================= 运行模块 1：新浪 API =================
    # 【模块化运行】：遍历配置中的所有新浪证券/外汇
    for api_code, config in TARGET_STOCKS.items():
        
        # 1. 尝试获取 API 数据 (返回字典)
        api_data = get_data_sina(api_code)
        
        # 2. 合并配置数据和 API 数据
        final_data = {
            "name": config["name"],
            "code": config["code"],
            "target_price": config["target_price"],
            "is_error": "error" in api_data,
            **api_data  # 合并 API 返回的所有键值对
        }
        all_stock_data.append(final_data)
        
    # ================= 运行模块 2：集思录 API =================
    # 【模块化运行】：遍历配置中的所有集思录指数
    for index_id, config in JISILU_TARGETS.items():
        
        # 1. 尝试获取 API 数据 (返回字典)
        api_data = get_data_jisilu()
        
        # 2. 合并配置数据和 API 数据
        final_data = {
            "name": config["name"],
            "code": config["code"],
            "target_price": config["target_price"],
            "is_error": "error" in api_data,
            **api_data  # 合并 API 返回的所有键值对
        }
        all_stock_data.append(final_data)
        
    # 3. 生成 HTML 内容
    html_content = create_html_content(all_stock_data)

    # 4. 写入文件
    try:
        # 文件仍写入当前工作目录的 index_price.html
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个证券/指数数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")
