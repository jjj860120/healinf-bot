import os
import anthropic
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))
claude = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

SYSTEM_PROMPT = """你是一個溫柔的情緒陪伴助手，專門陪伴高壓工作者恢復情緒狀態。

你的個性：
- 像一個懂你的朋友，不說教、不評判
- 用溫暖但不過度甜膩的語氣
- 簡短有力，不說廢話
- 說繁體中文

你的任務：
1. 先判斷對方現在的情緒狀態（焦慮、疲憊、空虛、憤怒、迷茫、壓力大、難過等）
2. 用一句話讓對方感覺「有人懂我」
3. 給一個當下可以做的小動作（30秒到3分鐘內能完成）
4. 給一句今日提醒

回應格式（不要加標題，直接說話）：
第一段：情緒共鳴（2-3句，讓人感覺被理解）
第二段：一個具體小動作（用emoji開頭）
第三段：一句今日提醒（溫柔但有力量）

注意：
- 總字數控制在150字以內
- 不要問太多問題，直接給出陪伴
- 不要說「我了解你的感受」這種空話
- 如果對方說的不像情緒相關，溫柔引導回來"""

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()
    
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_text}
            ]
        )
        reply = response.content[0].text
    except Exception as e:
        reply = "你說的我都收到了 💙\n現在先做一件事：慢慢吸氣 4 秒，吐氣 6 秒。\n你不需要現在解決所有事情。"
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
