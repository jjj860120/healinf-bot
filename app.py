import os
import random
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

# 療癒短語庫
HEALING_PHRASES = [
    "不是你不努力，而是你已經太久沒有真正休息了。",
    "今天不用逼自己進步，先恢復也可以。",
    "你的感覺是真實的，你不需要為此道歉。",
    "完成 70% 已經很好了，不是每件事都需要做到 100%。",
    "今天能撐過來，就已經很厲害了。",
    "疲憊不是你的弱點，是你太認真了的證明。",
    "你不需要一直很強，偶爾軟弱也沒關係。",
    "休息不是浪費時間，是為了走更長的路。",
    "慢一點沒關係，只要還在走就好。",
    "你值得被好好對待，包括被你自己好好對待。",
]

# 伸展動作
STRETCHES = [
    "🧘 肩頸放鬆\n雙手交扣放在後腦，輕輕向後施力，下巴微抬。保持 30 秒，感受頸部前側的伸展。",
    "🌬 深呼吸練習\n吸氣 4 秒 → 屏住 4 秒 → 呼氣 6 秒。重複 5 次，讓神經系統慢下來。",
    "🙆 胸口舒展\n雙手在背後十指交扣，肩膀往後夾，胸口打開，下巴微揚。保持 20 秒。",
    "🪑 久坐放鬆\n坐著，一腳抬起放在另一膝蓋上，身體微微前傾，感受臀部伸展。每邊 30 秒。",
    "✋ 護眼操\n閉眼，用溫熱的手掌輕敷眼睛 30 秒。再緩慢轉動眼球，上下左右各 5 次。",
]

# 今日能量
ENERGY = [
    "⚡ 今日能量\n今天你可能容易對自己要求太高。\n\n提醒自己：完成比完美更重要。\n\n✅ 適合：整理環境、散步、早睡\n🚫 避免：情緒性回訊息、熬夜",
    "💙 今日能量\n今天你可能容易想太多別人的感受。\n\n提醒自己：你的感受一樣重要。\n\n✅ 適合：獨處充電、聽音樂、泡澡\n🚫 避免：人多的場合、做重大決定",
    "🌱 今日能量\n今天是適合放慢腳步的一天。\n\n提醒自己：慢慢來，也是一種前進。\n\n✅ 適合：喝杯熱茶、讀幾頁書、早睡\n🚫 避免：同時處理太多事情",
]

def get_daily_response(user_feeling=None):
    phrase = random.choice(HEALING_PHRASES)
    stretch = random.choice(STRETCHES)
    energy = random.choice(ENERGY)
    return f"{phrase}\n\n{stretch}\n\n{energy}"

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
    
    if user_text in ['焦慮', '很累', '空虛', '沒動力', '腦袋很亂']:
        feeling_responses = {
            '焦慮': "感覺焦慮很正常，你的神經系統在保護你。\n\n🌬 先做三次深呼吸：吸氣 4 秒，呼氣 6 秒。\n\n你不需要現在解決所有問題。",
            '很累': "你真的很辛苦了。\n\n累是因為你一直在努力。今天可以允許自己什麼都不做。\n\n🛌 如果可以，提早一小時睡覺。",
            '空虛': "空虛感有時是心在說：我需要被滋養了。\n\n不是你有問題，是你需要補充能量。\n\n☕ 泡杯喜歡的飲料，靜靜坐著 10 分鐘。",
            '沒動力': "沒動力不代表你懶，可能只是油箱空了。\n\n不用強迫自己，先做一件最小的事：喝水。\n\n💧 就從喝水開始，今天這樣就夠了。",
            '腦袋很亂': "腦袋亂是因為你在意太多事情。\n\n試試這個：拿一張紙，把腦袋裡的事通通寫出來。不用整理，只是倒出來。\n\n✏️ 把它們放到紙上，不用放在腦子裡。",
        }
        reply = feeling_responses[user_text]
    elif '今日' in user_text or '能量' in user_text:
        reply = random.choice(ENERGY)
    elif '伸展' in user_text or '運動' in user_text:
        reply = random.choice(STRETCHES)
    else:
        reply = get_daily_response()
        reply = "嗨，我在這裡 💙\n\n告訴我你今天的狀態：\n\n😟 焦慮\n😴 很累\n😶 空虛\n😞 沒動力\n🤯 腦袋很亂\n\n或直接傳訊息，我陪你說說話。"
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
