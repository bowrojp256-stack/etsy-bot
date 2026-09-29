import os
import json
import base64
import requests
import xml.etree.ElementTree as ET
from flask import Flask, request, make_response
from openai import OpenAI
from WXBizMsgCrypt import WXBizMsgCrypt

app = Flask(__name__)

# 初始化 OpenAI 客户端
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# 环境变量配置
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID = os.environ.get("BITABLE_TABLE_ID", "")

WX_CORP_ID = os.environ.get("WX_CORP_ID", "")
WX_TOKEN = os.environ.get("WX_TOKEN", "")
WX_AES_KEY = os.environ.get("WX_AES_KEY", "")
WX_CORP_SECRET = os.environ.get("WX_CORP_SECRET", "")  # 自建应用的 Secret

def get_feishu_tenant_access_token():
    """获取飞书 token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        res = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).json()
        return res.get("tenant_access_token", "")
    except Exception as e:
        print("获取飞书 Token 异常:", str(e))
        return ""

def add_record_to_bitable(fields):
    """写入飞书多维表格"""
    token = get_feishu_tenant_access_token()
    if not token:
        print("写入飞书失败: 无有效 Token")
        return False
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    try:
        res = requests.post(url, headers=headers, json={"fields": fields})
        print("写入多维表格响应:", res.json())
        return res.status_code == 200
    except Exception as e:
        print("写入飞书表格异常:", str(e))
        return False

def get_wx_access_token():
    """获取企业微信 access_token"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WX_CORP_ID}&corpsecret={WX_CORP_SECRET}"
    try:
        res = requests.get(url).json()
        return res.get("access_token", "")
    except Exception as e:
        print("获取企微 Token 异常:", str(e))
        return ""

def download_wx_media_as_base64(media_id):
    """下载企业微信图片并转为 Base64"""
    token = get_wx_access_token()
    if not token:
        return None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}"
    try:
        res = requests.get(url)
        if res.status_code == 200:
            return base64.b64encode(res.content).decode('utf-8')
    except Exception as e:
        print("下载企微图片异常:", str(e))
    return None

def parse_image_with_openai(image_base64):
    """调用 OpenAI GPT-4o 识别订单"""
    prompt = """
    你是一个专业的数据提取助手。请从这张 Etsy 订单截图中提取以下信息，严格以 JSON 格式输出：
    {
        "出单日期": "YYYY-MM-DD",
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
    如果某个字段截图中没有显示，设为空字符串 ""。只输出合法 JSON，不要加任何 MarkDown 标签或额外解释。
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                ]
            }],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print("OpenAI 识别失败:", str(e))
        return None

@app.route('/', methods=['GET'])
def index():
    return "Etsy Assistant Service is running!"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    wxcpt = WXBizMsgCrypt(WX_TOKEN, WX_AES_KEY, WX_CORP_ID)
    msg_signature = request.args.get('msg_signature', '')
    timestamp = request.args.get('timestamp', '')
    nonce = request.args.get('nonce', '')

    # 1. 企微验证 URL (GET 请求)
    if request.method == 'GET':
        echostr = request.args.get('echostr', '')
        ret, sEchoStr = wxcpt.VerifyURL(msg_signature, timestamp, nonce, echostr)
        if ret == 0:
            # sEchoStr 如果是 bytes 格式，转为 str
            if isinstance(sEchoStr, bytes):
                sEchoStr = sEchoStr.decode('utf-8')
            resp = make_response(sEchoStr, 200)
            resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
            return resp
        else:
            print(f"企微 URL 验证失败，错误代码: {ret}")
            return f"VerifyURL Error: {ret}", 400

    # 2. 接收企微推送的消息 (POST 请求)
    if request.method == 'POST':
        ret, xml_content = wxcpt.DecryptMsg(request.data, msg_signature, timestamp, nonce)
        if ret != 0:
            print(f"企微消息解密失败，错误代码: {ret}")
            return "Decrypt Error", 400
        
        try:
            xml_tree = ET.fromstring(xml_content)
            msg_type = xml_tree.find('MsgType').text
            
            # 如果接收到的是图片消息
            if msg_type == 'image':
                media_id = xml_tree.find('MediaId').text
                print(f"收到图片消息，MediaId: {media_id}")
                image_base64 = download_wx_media_as_base64(media_id)
                if image_base64:
                    order_data = parse_image_with_openai(image_base64)
                    if order_data:
                        add_record_to_bitable(order_data)
        except Exception as e:
            print("解析企微消息异常:", str(e))
        
        return "success"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
