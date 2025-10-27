import requests
import os
import time
from datetime import datetime

# --- 配置 ---
# 修正：直接使用文件名，确保文件生成在 Actions 运行器的当前工作目录。
OUTPUT_FILE = "index_price.html"

# 自动刷新时间（秒）。30分钟 = 30 * 60 = 1800秒
REFRESH_INTERVAL = 1800  

# ==================== 价格获取函数 (沿用新浪API的健壮版本) ====================
def get_sz399975_price_sina():
    """使用新浪财经API获取SZ399975（证券公司指数）的实时价格。"""
    url = "http://hq.sinajs.cn/list=sz399975"
    headers = {
        # 保持 User-Agent 和 Referer 以模拟正常的浏览器请求
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'http://finance.sina.com.cn/'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10) 
        # 保持 gbk 编码，因为新浪 API 返回的数据通常是 gbk 编码的
        response.encoding = 'gbk'
        data = response.text
        
        if response.status_code != 200 or '="' not in data:
            return "获取失败", f"HTTP状态码: {response.status_code}"

        data_content = data.split('="')[1].strip('";')
        parts = data_content.split(',')
        
        if len(parts) >= 4:
            index_name = parts[0]
            current_price = parts[3]
            if current_price and current_price.replace('.', '', 1).isdigit():
                return index_name, current_price
            else:
                return "解析失败", "价格数据无效"
        else:
            return "解析失败", "数据项不足"
            
    except requests.exceptions.RequestException as e:
        return "网络错误", str(e)
    except Exception as e:
        return "未知错误", str(e)


def create_html_content(name, price):
    """生成带有价格和自动刷新功能的HTML内容。"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # <meta http-equiv="refresh"> 标签实现浏览器自动刷新
    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="{REFRESH_INTERVAL}">
    <title>{name} ({price})</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; margin-top: 50px; background-color: #f4f4f9; }}
        h1 {{ color: #2c3e50; font-size: 2.5em; }}
        #price_display {{ font-size: 6em; color: #27ae60; font-weight: bold; margin-top: 20px; border: 3px solid #27ae60; padding: 20px; display: inline-block; border-radius: 10px; }}
        .timestamp {{ color: #7f8c8d; margin-top: 30px; font-size: 1.2em; }}
        .note {{ color: #e74c3c; margin-top: 10px; }}
    </style>
</head>
<body>
    <h1>证券公司指数 (399975) 最新价格</h1>
    <div id="price_display">{price}</div>
    <div class="timestamp">更新时间: {timestamp}</div>
    <div class="note">注意：此页面每 {REFRESH_INTERVAL // 60} 分钟自动重新加载，以获取最新数据。</div>
</body>
</html>
"""
    return html_template

# --- 主逻辑 ---
if __name__ == "__main__":
    
    # 尝试获取价格
    index_name, current_price = get_sz399975_price_sina()

    # 如果获取成功，写入HTML文件
    if index_name not in ["获取失败", "解析失败", "网络错误", "未知错误"]:
        html_content = create_html_content(index_name, current_price)
    else:
        # 如果获取失败，写入错误提示
        error_message = f"数据获取失败！原因: {index_name}。详细: {current_price}"
        html_content = create_html_content("错误", error_message)
        
    try:
        # 直接使用文件名写入，文件将生成在 Actions 运行的当前工作目录
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"成功更新文件: {OUTPUT_FILE}，价格: {current_price}")
    except Exception as e:
        # 这里的 print 信息将输出到 GitHub Actions 日志中
        print(f"写入文件失败: {e}")
