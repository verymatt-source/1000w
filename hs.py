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
    """生成包含价格表格、目标比例、历史数据和自动刷新设置的 HTML 页面内容。"""
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

    # --- 新增的历史数据 HTML 块（直接定义在函数内部） ---
    historical_data_html = """
    <div class="historical-section">
        <h2>📊 附：上证指数5%以上跌幅记录</h2>
        <table class="historical-table">
            <tr>
                <th>日期</th>
                <th>上证指数跌幅</th>
                <th>主要背景与原因</th>
                <th>市场表现与后续影响</th>
            </tr>
            <tr>
                <td><strong>1996年12月16日</strong></td>
                <td>-9.91%</td>
                <td>1996年股市经历疯狂上涨，沪指一年内涨幅达140%。《人民日报》发表特约评论员文章《正确认识当前股票市场》，批评股市“非理性暴涨”，引发恐慌性抛售。</td>
                <td>沪指连续两日暴跌近20%，两市绝大多数股票跌停。此次暴跌后，市场进入调整期。</td>
            </tr>
            <tr>
                <td><strong>2007年2月27日</strong></td>
                <td>-8.84%</td>
                <td>2006-2007年A股处于大牛市，市场积累较大泡沫。触发因素包括市场传闻加征资本利得税、IPO加速（如中国平安上市）等，引发恐慌性抛售。</td>
                <td>此次暴跌引发了全球市场联动下跌，美股道琼斯指数在次日也跌超3%。</td>
            </tr>
            <tr>
                <td><strong>2007年5月30日</strong></td>
                <td>-6.50%</td>
                <td>财政部宣布上调证券交易印花税，从1‰上调至3‰，直接打击市场情绪，特别是对当时炒作火热的中小盘股形成精准打击。</td>
                <td>中小盘股连续跌停，一周内沪指跌近千点，被称为“5.30股灾”。但此次调整只是大牛市中的插曲，其后市场转向蓝筹股行情，并最终涨至6124点历史高点。</td>
            </tr>
            <tr>
                <td><strong>2008年6月10日</strong></td>
                <td>-7.73%</td>
                <td>全球金融危机蔓延，国内通胀高企，央行加息预期升温。此次暴跌发生在沪指从6124点历史高位回落的熊市过程中。</td>
                <td>沪指单日失守3000点，是2008年大熊市中的一次急跌。全年沪指从高点下跌超70%。</td>
            </tr>
            <tr>
                <td><strong>2015年1月19日</strong></td>
                <td>-7.70%</td>
                <td>监管层出手规范券商融资融券（两融）业务，引发杠杆资金平仓潮。</td>
                <td>券商股集体跌停，两市市值单日蒸发约3万亿元，被称为“119股灾”。</td>
            </tr>
            <tr>
                <td><strong>2015年6月19日</strong></td>
                <td>-6.42%</td>
                <td>A股在创下5178点新高后进入去杠杆周期，高杠杆融资盘恐慌性抛售。此前热门股“中国中车”崩盘成为压垮市场的导火索之一。</td>
                <td>此次暴跌开启了2015年下半年的股灾，沪指一周内多次跌幅超6%，近1100只股票跌停。</td>
            </tr>
            <tr>
                <td><strong>2016年1月4日</strong></td>
                <td>-6.86%</td>
                <td>当天是熔断机制正式实施的首个交易日。机制设计本身放大了市场恐慌情绪，导致流动性枯竭。</td>
                <td>沪深300指数暴跌并触发熔断机制，导致提前收盘。该机制在实施四天后被紧急叫停，成为A股“最短命”政策之一。</td>
            </tr>
            <tr>
                <td><strong>2019年5月6日</strong></td>
                <td>-5.58%</td>
                <td>中美贸易摩擦升级，美国宣布对2000亿美元中国商品加征关税。五一长假后首个交易日，市场以暴跌回应。</td>
                <td>沪指跌破2900点，创业板指跌幅近8%。</td>
            </tr>
            <tr>
                <td><strong>2020年2月3日</strong></td>
                <td>-7.72%</td>
                <td>疫情暴发市场对经济前景陷入极度恐慌。当日为春节后首个交易日。</td>
                <td>沪指重挫，两市超3000只个股跌停。但央行迅速释放流动性，市场随后快速反弹，创业板指年内涨幅超60%。</td>
            </tr>
            <tr>
                <td><strong>2022年4月25日</strong></td>
                <td>-5.13%</td>
                <td>多重利空叠加：美联储加息预期升温、俄乌冲突持续、国内疫情反复引发供应链中断担忧。外围市场前一日暴跌也加剧了恐慌。</td>
                <td>沪指跌破3000点关键心理关口，触发程序化交易止损盘，加剧抛售。两市近4600只个股下跌。</td>
            </tr>
            <tr>
                <td><strong>2025年4月7日</strong></td>
                <td>-7.34%</td>
                <td>美国宣布对全球加征“对等关税”，引发全球经济衰退担忧。此事件为搜索结果中提及的最新一次重大暴跌。</td>
                <td>根据资料显示，沪指单日蒸发数万亿市值，科技与出口板块遭重创，创业板指单日重挫12.5%。</td>
            </tr>
        </table>
        
        <h3>📝 回顾与规律：</h3>
        <p><strong>暴跌原因：</strong>主要包括政策调整（如1996年社论、2007年上调印花税）、去杠杆（如2015年）、外部冲击（如2008年金融危机、2019年贸易摩擦、2020年疫情）以及市场自身泡沫破裂。</p>
        <p><strong>事后影响：</strong>单日暴跌超过5%在A股历史上并不算非常频繁（尤其是剔除2008、2015等极端年份后），但其发生往往标志着阶段性顶部或底部的形成。统计显示，在暴跌之后，市场短期反弹的概率较高，且中长期（三个月、半年）来看，上涨的概率和平均收益都较为可观。但这并不意味着每次暴跌都是抄底机会，最终走势仍取决于当时的经济基本面和政策救市力度。</p>
    </div>
    """


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
        h2 {{ color: #2c3e50; font-size: 1.8em; margin-top: 50px; border-bottom: 2px solid #3498db; padding-bottom: 10px; display: inline-block; }} 
        h3 {{ color: #34495e; font-size: 1.4em; margin-top: 30px; }} 
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
        .historical-section {{ /* 用于新内容的样式 */
            width: 95%;
            margin: 50px auto; 
            padding: 20px;
            background-color: #ffffff;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.05);
        }}
        .historical-section p {{
            text-align: left;
            line-height: 1.6;
            margin-bottom: 20px;
        }}
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
    
    {historical_data_html} </body>
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







