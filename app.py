import os
import json
import base64
import requests
import xml.etree.ElementTree as ET
import time
import struct
import hashlib
from flask import Flask, request, make_response
from openai import OpenAI
from Crypto.Cipher import AES

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
WX_CORP_SECRET = os.environ.get("WX_CORP_SECRET", "")


# ------------------------------------------------------------------
# 企业微信加解密核心算法 (自包含，无需依赖外部 WXBizMsgCrypt 包)
# ------------------------------------------------------------------
class WXBizMsgCrypt:
    def __init__(self, sToken, sEncodingAESKey, sReceiveId):
        self.key = base64.b64decode(sEncodingAESKey + "=")
        self.token = sToken
        self.receiveId = sReceiveId

    def VerifyURL(self, sMsgSignature, sTimeStamp, sNonce, sEchoStr):
        sha1 = hashlib.sha1()
        sortlist = [self.token, sTimeStamp, sNonce, sEchoStr]
        sortlist.sort()
        sha1.update("".join(sortlist).encode('utf-8'))
        signature = sha1.hexdigest()

        if signature != sMsgSignature:
            return -40001, None

        try:
            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
            decrypted = cipher.decrypt(base64.b64decode(sEchoStr))
            pad = decrypted[-1]
            if pad < 1 or pad > 32:
                pad = 0
            decrypted = decrypted[:-pad]
            content = decrypted[16:]
            xml_len = struct.unpack(">I", content[:4])[0]
            xml_content = content[4:4 + xml_len].decode('utf-8')
            return 0, xml_content
        except Exception as e:
            print("VerifyURL 解密异常:", str(e))
            return -40002, None

    def DecryptMsg(self, sPostData, sMsgSignature, sTimeStamp, sNonce):
        try:
            xml_tree = ET.fromstring(sPostData)
            encrypt = xml_tree.find("Encrypt").text

            sha1 = hashlib.sha1()
            sortlist = [self.token, sTimeStamp, sNonce, encrypt]
            sortlist.sort()
            sha1.update("".join(sortlist).encode('utf-8'))
            signature = sha1.hexdigest()

            if signature != sMsgSignature:
                return -40001, None

            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
            decrypted = cipher.decrypt(base64.b64decode(encrypt))
            pad = decrypted[-1]
            if pad < 1 or pad > 32:
                pad = 0
            decrypted = decrypted[:-pad]
            content = decrypted[16:]
            xml_len = struct.unpack(">I", content[:4])[0]
            xml_content = content[4:4 + xml_len].decode('utf-8')
            return 0, xml_content
        except Exception as e:
            print("DecryptMsg 异常:", str(e))
            return -40002, None


# ------------------------------------------------------------------
# 业务逻辑：飞书 + 企微 + OpenAI
# ------------------------------------------------------------------
def get_feishu_tenant_access_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        res = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).json()
        return res.get("tenant_access_token", "")
    except Exception as e:
        print("获取飞书 Token 异常:", str(e))
        return ""

def add_record_to_bitable(fields):
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
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WX_CORP_ID}&corpsecret={WX_CORP_SECRET}"
    try:
        res = requests.get(url).json()
        return res.get("access_token", "")
    except Exception as e:
        print("获取企微 Token 异常:", str(e))
        return ""

def download_wx_media_as_base64(media_id):
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
    如果某个字段截图中没有显示，设为空字符串 ""。只输出合法 JSON，不要加任何 MarkDown 标签。
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
