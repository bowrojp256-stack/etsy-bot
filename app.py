import os
import json
import base64
import requests
import threading
import traceback
from flask import Flask, request, jsonify
import xml.etree.ElementTree as ET
from WXBizMsgCrypt3 import WXBizMsgCrypt

app = Flask(__name__)

# ================= 环境变量读取 =================
WX_TOKEN = os.environ.get("WX_TOKEN")
WX_AES_KEY = os.environ.get("WX_AES_KEY")
WX_CORP_ID = os.environ.get("WX_CORP_ID")
WX_CORP_SECRET = os.environ.get("WX_CORP_SECRET")  # 用于获取 access_token 下载图片

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
FEISHU_BITABLE_APP_TOKEN = os.environ.get("FEISHU_BITABLE_APP_TOKEN")
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID")

# 初始化企业微信加解密类
wxcpt = WXBizMsgCrypt(WX_TOKEN, WX_AES_KEY, WX_CORP_ID)

# ================= 辅助函数 =================

def get_wx_access_token():
    """获取企业微信 access_token（用于下载图片消息）"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WX_CORP_ID}&corpsecret={WX_CORP_SECRET}"
    res = requests.get(url).json()
    if res.get("errcode") == 0:
        return res.get("access_token")
    else:
        print(f"❌ 获取企业微信 access_token 失败: {res}")
        return None

def download_wx_media(media_id):
    """根据 media_id 从企业微信下载图片并转为 Base64"""
    token = get_wx_access_token()
    if not token:
        return None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}"
    res = requests.get(url)
    if res.status_code == 200:
        return base64.b64encode(res.content).decode("utf-8")
    else:
        print(f"❌ 下载企微图片失败，状态码: {res.status_code}")
        return None

def get_feishu_access_token():
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    res = requests.post(url, json=payload).json()
    if res.get("code") == 0:
        return res.get("tenant_access_token")
    else:
        print(f"❌ 获取飞书 access_token 失败: {res}")
        return None

def write_to_feishu_bitable(data_dict):
    """把解析出的结构化数据写入飞书多维表格"""
    token = get_feishu_access_token()
    if not token:
        return False
    
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    payload = {
        "fields": data_dict
    }
    res = requests.post(url, headers=headers, json=payload).json()
    if res.get("code") == 0:
        print("🎉 [成功] 已成功写入飞书多维表格！")
        return True
    else:
        print(f"❌ [失败] 写入飞书表格报错: {res}")
        return False

def analyze_image_with_openai(base64_image):
    """使用 OpenAI Vision 模型识别图片内容"""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }
    
    prompt = "请分析这张截图，提取关键信息并以严格的 JSON 格式返回。不要包含任何 markdown 标签或多余解释。"
    
    payload = {
        "model": "gpt-4o",  # 使用支持视觉的 gpt-4o
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 1000
    }
    
    res = requests.post(url, headers=headers, json=payload).json()
    if "choices" in res:
        content = res["choices"][0]["message"]["content"]
        return json.loads(content)
    else:
        print(f"❌ 调用 OpenAI 失败: {res}")
        return None

# ================= 核心后台异步任务 =================

def process_image_in_background(media_id):
    """后台异步处理图片、调用 AI 并写入飞书（包含完整错误捕捉）"""
    print(f"\n🚀 [后台任务开始] 正在处理图片，MediaId: {media_id}")
    try:
        # 1. 下载图片
        print("1️⃣ 正在从企业微信下载图片...")
        base64_img = download_wx_media(media_id)
        if not base64_img:
            print("❌ 图片下载失败，终止后续任务")
            return

        # 2. 调用 OpenAI 分析
        print("2️⃣ 正在调用 OpenAI 进行图片分析...")
        extracted_data = analyze_image_with_openai(base64_img)
        if not extracted_data:
            print("❌ OpenAI 分析未返回有效数据，终止后续任务")
            return
        print(f"💡 AI 解析结果: {extracted_data}")

        # 3. 写入飞书表格
        print("3️⃣ 正在写入飞书多维表格...")
        write_to_feishu_bitable(extracted_data)

    except Exception as e:
        print(f"\n❌❌❌ [后台线程崩溃报错] 原因: {str(e)}")
        print("👇 详细错误堆栈 Traceback:")
        traceback.print_exc()
        print("----------------------------------------\n")

# ================= 路由入口 =================

@app.route('/')
def index():
    return "Etsy Assistant Service is Running."

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    # 企微验证 URL 专用 GET 请求
    if request.method == 'GET':
        msg_signature = request.args.get('msg_signature', '')
        timestamp = request.args.get('timestamp', '')
        nonce = request.args.get('nonce', '')
        echostr = request.args.get('echostr', '')
        
        ret, sEchoStr = wxcpt.VerifyURL(msg_signature, timestamp, nonce, echostr)
        if ret == 0:
            return sEchoStr
        else:
            print(f"❌ 企微验证 URL 失败，错误码: {ret}")
            return "Verify failed", 400

    # 企微接收用户消息 POST 请求
    elif request.method == 'POST':
        msg_signature = request.args.get('msg_signature', '')
        timestamp = request.args.get('timestamp', '')
        nonce = request.args.get('nonce', '')
        req_data = request.data

        ret, xml_content = wxcpt.DecryptMsg(req_data, msg_signature, timestamp, nonce)
        if ret != 0:
            print(f"❌ 消息解密失败，错误码: {ret}")
            return "Decrypt failed", 400

        # 解析解密后的 XML
        root = ET.fromstring(xml_content)
        msg_type = root.find('MsgType').text

        if msg_type == 'image':
            media_id = root.find('MediaId').text
            print(f"\n📸 [收到图片消息] MediaId: {media_id}")
            
            # 开启子线程异步处理，主线程立即响应 200 避免企微 5 秒超时
            thread = threading.Thread(target=process_image_in_background, args=(media_id,))
            thread.start()
            
        elif msg_type == 'text':
            content = root.find('Content').text
            print(f"💬 [收到文本消息]: {content}")
        
        # 立即回复 success / 空串告知企微服务端已接收
        return "success"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
