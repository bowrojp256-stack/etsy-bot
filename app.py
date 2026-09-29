import os
import json
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# ==================== 配置区 ====================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET")

# 你的飞书多维表格 App Token 和 Table ID
FEISHU_APP_TOKEN = "Axv4bk0CzaCpVgsLYqhcXJRAnSc"
FEISHU_TABLE_ID = "tblofTZfGQ8ac3xN"

ai_client = OpenAI(api_key=OPENAI_API_KEY)

# ==================== 1. 飞书 API 交互工具 ====================
def get_feishu_access_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    res = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).json()
    return res.get("tenant_access_token")

def feishu_add_field(field_name, field_type=1):
    """动态工具：在飞书多维表格中新增一列"""
    token = get_feishu_access_token()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/fields"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"field_name": field_name, "type": field_type}
    res = requests.post(url, headers=headers, json=payload).json()
    if res.get("code") == 0:
        return f"成功在飞书表格中新增列【{field_name}】！"
    return f"新增列失败: {res.get('msg')}"

def feishu_upsert_order(order_data):
    """数据工具：写入或更新订单（自动排除物流字段，保持空白）"""
    token = get_feishu_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    order_id = order_data.get("order_id")
    if not order_id:
        return "未能识别到有效的订单号，无法写入。"

    # 查重：按“订单号”检索
    search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records/search"
    search_payload = {
        "filter": {
            "conjunction": "and",
            "conditions": [{"field_name": "订单号", "operator": "is", "value": [str(order_id)]}]
        }
    }
    search_res = requests.post(search_url, headers=headers, json=search_payload).json()
    items = search_res.get("data", {}).get("items", [])

    # 映射表头字段（运输公司和物流单号强制保持为空，留给人工填写）
    fields = {
        "出单日期": str(order_data.get("order_date") or ""),
        "订单号": str(order_id),
        "运输公司": "",
        "物流单号": "",
        "收件人": str(order_data.get("recipient") or ""),
        "地址": str(order_data.get("address") or ""),
        "城市": str(order_data.get("city") or ""),
        "省份": str(order_data.get("state") or ""),
        "邮编": str(order_data.get("zip_code") or ""),
        "国家": str(order_data.get("country") or ""),
        "电话": str(order_data.get("phone") or ""),
        "邮箱": str(order_data.get("email") or ""),
        "SKU": str(order_data.get("sku") or ""),
        "定制": str(order_data.get("customization") or ""),
        "类型": str(order_data.get("type") or ""),
        "尺寸": str(order_data.get("size") or "")
    }

    if items:
        record_id = items[0]["record_id"]
        update_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records/{record_id}"
        requests.put(update_url, headers=headers, json={"fields": fields})
        return f"订单 {order_id} 数据已在飞书更新！"
    else:
        add_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
        requests.post(add_url, headers=headers, json={"fields": fields})
        return f"订单 {order_id} 已成功新增录入飞书表格！"

def feishu_search_order(order_id):
    """查询工具：检索订单详情"""
    token = get_feishu_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    search_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records/search"
    search_payload = {
        "filter": {
            "conjunction": "and",
            "conditions": [{"field_name": "订单号", "operator": "is", "value": [str(order_id)]}]
        }
    }
    res = requests.post(search_url, headers=headers, json=search_payload).json()
    items = res.get("data", {}).get("items", [])
    if items:
        return json.dumps(items[0]["fields"], ensure_ascii=False)
    return f"未找到订单号为 {order_id} 的记录。"

# ==================== 2. Agent 函数调用配置 ====================
tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "feishu_add_field",
            "description": "当用户指示要在飞书表格中增加新列/新表头时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string", "description": "要新增的列名"}
                },
                "required": ["field_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "feishu_upsert_order",
            "description": "解析订单截图并写入或更新到飞书表格",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_date": {"type": "string"},
                    "order_id": {"type": "string"},
                    "recipient": {"type": "string"},
                    "address": {"type": "string"},
                    "city": {"type": "string"},
                    "state": {"type": "string", "description": "利用地理知识根据城市和邮编自动推导补充的省份/州"},
                    "zip_code": {"type": "string"},
                    "country": {"type": "string", "description": "统一转换为标准 ISO 两位大写代码，如 US, DE, GB, CA"},
                    "phone": {"type": "string"},
                    "email": {"type": "string"},
                    "sku": {"type": "string"},
                    "customization": {"type": "string"},
                    "type": {"type": "string"},
                    "size": {"type": "string"}
                },
                "required": ["order_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "feishu_search_order",
            "description": "按订单号查询表格数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单号"}
                },
                "required": ["order_id"]
            }
        }
    }
]

def run_agent_workflow(user_text, image_url=None):
    system_instruction = (
        "你是一个专业的 Etsy 订单管理助手。\n"
        "1. 处理订单截图时：必须提取数据并调用 `feishu_upsert_order`。\n"
        "2. 国家字段（country）：必须转换为标准的 ISO 两位大写字母缩写（如 Germany->DE, United States->US）。\n"
        "3. 省份字段（state）：若原图无省份，必须根据城市名和邮编自动推理补全（如 71263 Weil der Stadt 补全为 Baden-Württemberg）。\n"
        "4. 忽略任何运输公司和物流单号（包括 'USPS Verified' 字样，切勿将其误判为快递公司）。\n"
        "5. 若用户要在表格里加列，调用 `feishu_add_field`；若用户查询订单，调用 `feishu_search_order`。"
    )

    messages = [{"role": "system", "content": system_instruction}]
    user_content = []
    
    if user_text:
        user_content.append({"type": "text", "text": user_text})
    if image_url:
        user_content.append({"type": "image_url", "image_url": {"url": image_url}})
        
    messages.append({"role": "user", "content": user_content})

    response = ai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools_schema,
        tool_choice="auto"
    )

    response_message = response.choices[0].message
    tool_calls = response_message.tool_calls

    if tool_calls:
        for tool_call in tool_calls:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if func_name == "feishu_add_field":
                return feishu_add_field(args.get("field_name"))
            elif func_name == "feishu_upsert_order":
                return feishu_upsert_order(args)
            elif func_name == "feishu_search_order":
                return feishu_search_order(args.get("order_id"))

    return response_message.content

# ==================== 3. 路由入口 ====================
@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.json or {}
    text = data.get("text", "")
    image_url = data.get("image_url")

    if not text and not image_url:
        return jsonify({"status": "fail", "reason": "缺少 text 或 image_url 参数"}), 400

    reply = run_agent_workflow(text, image_url)
    return jsonify({"status": "success", "reply": reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)