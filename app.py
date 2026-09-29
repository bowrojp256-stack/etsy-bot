import os
import json
import base64
import socket
import struct
import xml.etree.ElementTree as ET
import requests
import threading
import traceback
from flask import Flask, request
from Crypto.Cipher import AES

app = Flask(__name__)

# ================= 环境变量读取 =================
WX_TOKEN = os.environ.get("WX_TOKEN", "")
WX_AES_KEY = os.environ.get("WX_AES_KEY", "")
WX_CORP_ID = os.environ.get("WX_CORP_ID", "")
WX_CORP_SECRET = os.environ.get("WX_CORP_SECRET", "")  # 企业微信应用Secret，用于下载图片

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_BITABLE_APP_TOKEN = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "")
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "")

# ================= 企业微信内置加解密类（避免依赖外部文件） =================

class WXBizMsgCrypt:
    def __init__(self, token, encoding_aes_key, receive_id):
        self.token = token
        self.receive_id = receive_id
        self.key = base64.b64decode(encoding_aes_key + "=")

    def verify_signature(self, msg_signature, timestamp, nonce, echo_str):
        import hashlib
        sort_list = [self.token, timestamp, nonce, echo_str]
        sort_list.sort()
        sha1 = hashlib.sha1()
        sha1.update("".join(sort_list).encode('utf-8'))
        return sha1.hexdigest() == msg_signature

    def decrypt(self, encrypt_msg):
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        decrypted = cipher.decrypt(base64.b64decode(encrypt_msg))
        
        # 去除 PKCS7 填充
        pad = decrypted[-1]
        if pad < 1 or pad > 32:
            pad = 0
        decrypted = decrypted[:-pad]
        
        # 解析数据包结构: 16字节随机数 + 4字节长度 + content + receive_id
        content = decrypted[16:]
        msg_len = socket.ntohl(struct.unpack("I", content[:4])[0])
        xml_content = content[4:4 + msg_len].decode('utf-8')
        from_id = content[4 + msg_len:].decode('utf-8')
        
        if from_id != self.receive_id:
            raise ValueError("CorpID 不匹配")
        return xml_content

    def DecryptMsg(self, sPostData, sMsgSignature, sTimeStamp, sNonce):
        root = ET.fromstring(sPostData)
        encrypt = root.find('Encrypt').text
        if not self.verify_signature(sMsgSignature, sTimeStamp, sNonce, encrypt):
            return -40001, None
        xml_content = self.decrypt(encrypt)
        return 0, xml_content

    def VerifyURL(self, sMsgSignature, sTimeStamp, sNonce, sEchoStr):
        if not self.verify_signature(sMsgSignature, sTimeStamp, sNonce, sEchoStr):
            return -40001, None
        echo_str = self.decrypt(sEchoStr)
        return 0, echo_str

# 实例化加解密工具
wxcpt = WXBizMsgCrypt(WX_TOKEN, WX_AES_KEY, WX_CORP_ID)

# ================= API 交互辅助函数 =================

def get_wx_access_token():
    """获取企微 access_token"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WX_CORP_ID}&corpsecret={WX_CORP_SECRET}"
    res = requests.get(url).json()
    if res.get("errcode") == 0:
        return res.get("access_token")
    print(f"❌ 获取企微 access_token 失败: {res}")
    return None

def download_wx_media(media_id):
    """从企微服务器下载图片并转换为 Base64"""
    token = get_wx_access_token()
    if not token:
        return None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}"
    res = requests.get(url)
    if res.status_code == 200:
        return base64.b64encode(res.content).decode("utf-8")
    print(f"❌ 下载图片失败，HTTP状态码: {res.status_code}")
    return None

def get_feishu_access_token():
    """获取飞书 access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    res = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).json()
    if res.get("code") == 0:
        return res.get("tenant_access_token")
    print(f"❌ 获取飞书 access_token 失败: {res}")
    return None

def write_to_feishu_bitable(data_dict):
    """写入飞书多维表格"""
    token = get_feishu_access_token()
    if not token:
        return False
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    res = requests.post(url, headers=headers, json={"fields": data_dict}).json()
    if res.get("code") == 0:
        print("🎉 [成功] 已成功写入飞书多维表格！")
        return True
    print(f"❌ 写入飞书表格报错: {res}")
    return False

def analyze_image_with_openai(base64_image):
    """调用 OpenAI Vision 模型识别图片内容"""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    prompt = "请分析这张截图，提取关键信息并以 JSON 格式返回。不要包含任何 markdown 标记。"
    
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
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
    print(f"❌ 调用 OpenAI 失败: {res}")
    return None

# ================= 后台异步任务 =================

def process_image_in_background(media_id):
    """后台异步下载图片、解析并写入飞书（全套异常捕获）"""
    print(f"\n🚀 [后台任务开始] 正在处理图片，MediaId: {media_id}")
    try:
        print("1️⃣ 正在从企业微信下载图片...")
        base64_img = download_wx_media(media_id)
        if not base64_img:
            return

        print("2️⃣ 正在调用 OpenAI 进行图片分析...")
        extracted_data = analyze_image_with_openai(base64_img)
        if not extracted_data:
            return
        print(f"💡 AI 解析结果: {extracted_data}")

        print("3️⃣ 正在写入飞书多维表格...")
        write_to_feishu_bitable(extracted_data)

    except Exception as e:
        print(f"\n❌❌❌ [后台线程报错]: {str(e)}")
        traceback.print_exc()

# ================= 路由入口 =================

@app.route('/')
def index():
    return "Etsy Assistant Service is Running."

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        msg_signature = request.args.get('msg_signature', '')
        timestamp = request.args.get('timestamp', '')
        nonce = request.args.get('nonce', '')
        echostr = request.args.get('echostr', '')
        ret, sEchoStr = wxcpt.VerifyURL(msg_signature, timestamp, nonce, echostr)
        if ret == 0:
            return sEchoStr
        return "Verify failed", 400

    elif request.method == 'POST':
        msg_signature = request.args.get('msg_signature', '')
        timestamp = request.args.get('timestamp', '')
        nonce = request.args.get('nonce', '')
        
        ret, xml_content = wxcpt.DecryptMsg(request.data, msg_signature, timestamp, nonce)
        if ret != 0:
            return "Decrypt failed", 400

        root = ET.fromstring(xml_content)
        msg_type = root.find('MsgType').text

        if msg_type == 'image':
            media_id = root.find('MediaId').text
            print(f"\n📸 [收到图片消息] MediaId: {media_id}")
            # 开启异步线程处理图片
            threading.Thread(target=process_image_in_background, args=(media_id,)).start()
        elif msg_type == 'text':
            content = root.find('Content').text
            print(f"💬 [收到文本消息]: {content}")
        
        return "success"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
