import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# 初始化 OpenAI 客户端
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# 飞书多维表格配置 (可在 Render Environment 中配置，或在此填入默认值)
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

@app.route('/', methods=['GET'])
def index():
    return "Etsy Assistant Service is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}
    
    # -------------------------------------------------------------
    # 1. 飞书 URL 校验 Handshake (核心关键：解决 Challenge code 没有返回)
    # -------------------------------------------------------------
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})
    
    # -------------------------------------------------------------
    # 2. Etsy 订单与事件处理逻辑
    # -------------------------------------------------------------
    event_type = data.get("header", {}).get("event_type") or data.get("type")
    
    # 如果是事件推送（如订单创建/消息变更）
    if data.get("event"):
        event_data = data.get("event", {})
        print("收到事件通知:", event_data)
        
        # 示例：提取订单信息或客户留言
        buyer_message = event_data.get("message", "客户购买了商品，请分析需求")
        
        # 调用 OpenAI 进行分析
        try:
            completion = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一个 Etsy 订单管理助手，请总结客户的需求和重点。"},
                    {"role": "user", "content": buyer_message}
                ]
            )
            ai_analysis = completion.choices[0].message.content
        except Exception as e:
            ai_analysis = f"AI 分析失败: {str(e)}"

        # 写入飞书多维表格（取消注释并确保环境变量填写正确）
        # add_record_to_bitable({
        #     "订单内容": buyer_message,
        #     "AI分析": ai_analysis
        # })

    return jsonify({"code": 0, "msg": "success"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
