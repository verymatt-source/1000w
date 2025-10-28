import requests
import os
from datetime import datetime
from operator import itemgetter # 用于列表排序操作
import calendar # 用于辅助判断周末/交易日
import json # 保留用于未来的配置扩展或简单日志

# --- 全局配置 ---
OUTPUT_FILE = "index_price.html"  # 最终生成的 HTML 报告文件名
REFRESH_INTERVAL = 300  # HTML 页面自动刷新间隔（秒），即 5 分钟

# ======================= 【核心配置区域】所有监控标的配置 =======================

# ALL_TARGET_CONFIGS：集中配置所有监控标的的信息。
# key: 标的内部唯一代码，用于日志和 HTML 展示
# type: 数据采集方式 ('SINA')
# api_code: 实际用于新浪 API 查询的代码
# target_price: 目标价格阈值
# note: 标的备注说明

ALL_TARGET_CONFIGS = {
    # 上证指数 (内部代码 SSEC)
    "SSEC": {
        "name": "上证指数",
        "type": "SINA",
        "api_code": "sh000001",  # 新浪 API 的上证指数代码
        "target_price": 3000.00, 
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
    
    # 【未来可在此处新增更多 SINA 标的】
    # "NEW_STOCK": {
    #     "name": "新增股票/指数",
    #     "type": "SINA", 
    #     "api_code": "shxxxxxx",
    #     "target_price": 10.00,  
    #     "note": "/暂无"         
    # }
}

# =========================================================================


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
        
        # 精简后的解析逻辑，保留对指数和股票（价格在 parts[3]）的兼容性。
        if len(parts) >= 4:
            current_price = parts[3]
            if current_price and current_price.replace('.', '', 1).isdigit():
                return {
                    "current_price": float(current_price),
                    "open_price": float(parts[1]) if len(parts) > 1 and parts[1].replace('.', '', 1).isdigit() else None,
                    "prev_close": float(parts[2]) if len(parts) > 2 and parts[2].replace('.', '', 1).isdigit() else None,
                }
        
        # 兼容外汇数据（价格在 parts[3]） - 尽管已移除外汇配置，但保留逻辑以防 future 标的加入
        elif stock_api_code.startswith('fx_') and len(parts) >= 4 and parts[3].replace('.', '', 1).isdigit():
             return {
                "current_price": float(parts[3]),
                "open_price": float(parts[0]) if len(parts) > 0 and parts[0].replace('.', '', 1).isdigit() else None,
                "prev_close": float(parts[1]) if len(parts) > 1 and parts[1].replace('.', '', 1).isdigit() else None,
            }

        return {"error": "解析失败", "detail": "数据项不足或价格数据无效"}
        
    except requests.exceptions.RequestException as e:
        return {"error": "网络错误", "detail": str(e)}
    except Exception as e:
        return {"error": "未知错误", "detail": str(e)}


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
    global REFRESH_INTERVAL
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S (北京时间)')
    table_rows = []
    if is_trading_time():
        status_text = '<span style="color: #27ae60;">正常运行 (交易时间)</span>'
    else:
        status_text = '<span style="color: #e67e22;">非交易时间</span>'
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
            # 统一展示为 3 位小数，方便指数/股票
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
    <title>价格监控数据展示</title>
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
    <h1>价格监控数据展示 (按目标比例排序)</h1>
    
    <table>
        {table_content}
    </table>

    <div class="timestamp">数据更新时间: {timestamp_with_status}</div>
    <div class="note">
        <p>📌 **运行说明**：本代码由 GitHub Actions 在**交易日**运行，页面每 {REFRESH_INTERVAL // 60} 分钟自动刷新。</p>
    </div>
</body>
</html>
"""
    return html_template


# --- 主逻辑部分 ---
if __name__ == "__main__":
    
    all_stock_data = [] # 存储所有标的最终处理结果的列表
    
    print("--- 开始采集新浪 API 数据 ---")
    
    # 1. 遍历配置，采集数据并组装
    for code, config in ALL_TARGET_CONFIGS.items():
        
        api_data = {}
        
        if config['type'] == 'SINA':
            api_data = get_data_sina(config["api_code"])
            
        else:
             api_data = {"error": "配置类型错误", "detail": f"不支持的类型: {config['type']}"}
        
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

        all_stock_data.append(final_data)
        
    # 2. 计算目标比例并排序
    
    # 计算目标比例 (Target Ratio): (当前价位 - 目标价位) / 当前价位
    for item in all_stock_data:
        item['target_ratio'] = None 
        
        if not item['is_error'] and item['current_price'] is not None and item['current_price'] != 0:
            current_price = item['current_price']
            target_price = item['target_price']
            # 使用目标价位作为分母，更符合常说的 "偏离目标价的百分比"
            item['target_ratio'] = (current_price - target_price) / target_price
        
    # 按目标比例绝对值升序排序 (绝对值最小排在最前，即最接近目标价)
    all_stock_data.sort(key=lambda x: abs(x['target_ratio']) if x['target_ratio'] is not None else float('inf'))


    # 3. 生成 HTML 文件
    
    html_content = create_html_content(all_stock_data) # 生成最终的 HTML 报告

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，包含 {len(all_stock_data)} 个证券/指数数据。")
    except Exception as e:
        print(f"写入文件失败: {e}")
