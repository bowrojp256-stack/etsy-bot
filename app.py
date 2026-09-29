import os
import json
import base64
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# 初始化 OpenAI 客户端
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# 环境变量配置
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID = os.environ.get("BITABLE_TABLE_ID", "")

def get_feishu_tenant_access_token():
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    response = requests.post(url, json=payload)
    data = response.json()
    return data.get("tenant_access_token", "")

def add_record_to_bitable(fields):
    """向飞书多维表格插入一条记录"""
    token = get_feishu_tenant_access_token()
    if not token:
        print("获取飞书 token 失败")
        return False
        
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    payload = {"fields": fields}
    res = requests.post(url, headers=headers, json=payload)
    print("写入多维表格结果:", res.json())
    return res.status_code == 200

def parse_image_with_openai(image_base64):
    """调用 OpenAI 识别图片中的订单数据"""
    prompt = """
    你是一个专业的数据提取助手。请从这张 Etsy 订单截图中提取以下信息，并严格以 JSON 格式输出：
    {
        "出单日期": "YYYY-MM-DD，若没有年份默认今年",
        "订单号": "",
        "运输公司": "",
        "物流单号": "",
        "收件人": "",
        "地址": "",
        "城市": "",
        "省份": "",
        "邮编": "",
        "国家": "",
        "电话": "",
        "邮箱": "",
        "SKU": "",
        "定制信息": "",
        "类型": "",
        "尺寸": ""
    }
    注意：
    1. 如果某个字段截图中没有显示或无法确认，设为空字符串 ""。
    2. 只输出合法 JSON 字符串，不要添加任何 Markdown 格式或额外解释。
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # 使用具备视觉能力的模型
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            response_format={"type": "json_object"}
        )
        result_text = response.choices[0].message.content
        return json.loads(result_text)
    except Exception as e:
        print("OpenAI 识别失败:", str(e))
        return None

@app.route('/', methods=['GET'])
def index():
    return "Etsy Order Processing Service is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}
    
    # 1. 飞书/企业微信 URL 校验 Handshake
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})
    
    # 2. 接收图片并处理（企业微信/机器人推送格式）
    # 提示：如果是接收 Base64 编码的图片数据或 URL，在此处解析图片并传给 OpenAI
    image_base64 = data.get("image_base64")
    
    if image_base64:
        order_info = parse_image_with_openai(image_base64)
        if order_info:
            success = add_record_to_bitable(order_info)
            if success:
                return jsonify({"code": 0, "msg": "成功提取并写入飞书表格"})
            else:
                return jsonify({"code": 500, "msg": "写入飞书表格失败"})
        else:
            return jsonify({"code": 400, "msg": "图片解析失败"})

    return jsonify({"code": 0, "msg": "接收成功，无图片数据需要处理"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
