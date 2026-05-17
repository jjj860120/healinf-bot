import os
import json
import random
import re
import ephem
import pytz
from datetime import datetime
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# Google Sheets 初始化
def init_sheets():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    creds_dict = json.loads(creds_json)
    scopes = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(os.environ.get('GOOGLE_SHEET_ID')).sheet1
    return sheet

def get_user(sheet, user_id):
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if str(row.get('user_id','')) == str(user_id):
            return i+2, row
    return None, None

def save_user(sheet, user_id, data):
    row_num, existing = get_user(sheet, user_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    data['last_updated'] = now
    data['user_id'] = user_id
    if row_num:
        sheet.update(f'A{row_num}:G{row_num}', [[
            data.get('user_id',''),
            data.get('emotion_summary',''),
            data.get('state','0'),
            data.get('birthday',''),
            data.get('last_updated',''),
            data.get('name',''),
            data.get('recent_messages','')
        ]])
    else:
        sheet.append_row([
            data.get('user_id',''),
            data.get('emotion_summary',''),
            data.get('state','0'),
            data.get('birthday',''),
            now,
            data.get('name',''),
            data.get('recent_messages','')
        ])

def get_recent_messages(user_data):
    try:
        msgs = user_data.get('recent_messages','')
        if not msgs:
            return []
        return json.loads(msgs)
    except:
        return []

def update_recent_messages(messages, role, content):
    messages.append({'role': role, 'content': content})
    if len(messages) > 5:
        messages = messages[-5:]
    return messages

# 風格系統
STYLES = [
    '溫柔陪伴',
    '幽默紓壓',
    '簡短有力',
    '詩意療癒',
    '好奇探索',
    '理性梳理',
    '故事引導',
    '靜默同在',
    '當頭棒喝'
]

ACTIONS = [
    '呼吸練習',
    '身體伸展',
    '感官體驗',
    '環境整理',
    '書寫釋放',
    '什麼都不做',
    '喝一杯水',
    '出去走走',
    '聽一首歌',
    '看窗外一分鐘'
]

ENDINGS = [
    '有力量的一句話',
    '小小期許',
    '自然停在陪伴',
    '輕輕反問',
    '今日一個字',
    '明天的小期待'
]

EMOTION_WEIGHTS = {
    '焦慮': {'styles':['靜默同在','溫柔陪伴'],'actions':['呼吸練習','看窗外一分鐘']},
    '疲憊': {'styles':['溫柔陪伴','簡短有力'],'actions':['什麼都不做','身體伸展']},
    '空虛': {'styles':['故事引導','詩意療癒'],'actions':['感官體驗','聽一首歌']},
    '憤怒': {'styles':['理性梳理','幽默紓壓'],'actions':['出去走走','書寫釋放']},
    '難過': {'styles':['溫柔陪伴','靜默同在'],'actions':['喝一杯水','什麼都不做']},
    '逃避': {'styles':['當頭棒喝'],'actions':['書寫釋放','環境整理']},
    '拖延': {'styles':['當頭棒喝','理性梳理'],'actions':['環境整理','書寫釋放']},
}

WAKEUP_TRIGGERS = ['罵我','當頭棒喝','說真的','不要安慰我','直接說']

def get_combo(user_text, emotion_summary, recent_messages):
    # 用戶主動要求當頭棒喝
    for trigger in WAKEUP_TRIGGERS:
        if trigger in user_text:
            return '當頭棒喝', random.choice(ACTIONS), random.choice(ENDINGS)

    # 重複困境偵測
    if emotion_summary and len(recent_messages) >= 3:
        keywords = ['又','還是','一直','每次','老是']
        for kw in keywords:
            if kw in user_text:
                return '當頭棒喝', random.choice(ACTIONS), random.choice(ENDINGS)

    # 情緒權重
    for key in EMOTION_WEIGHTS:
        if key in user_text or key in emotion_summary:
            style = random.choice(EMOTION_WEIGHTS[key]['styles'])
            action = random.choice(EMOTION_WEIGHTS[key]['actions'])
            ending = random.choice(ENDINGS)
            return style, action, ending

    return random.choice(STYLES[:8]), random.choice(ACTIONS), random.choice(ENDINGS)

# Prompts
EMPATHY_PROMPT = """你是心緒，一個溫柔的情緒陪伴助手。

語氣風格：
- 像一個真正在聽的朋友，不急著給答案
- 說出用戶沒說出口的感受
- 用推測語氣「是不是⋯⋯」「可能有一種⋯⋯」
- 不超過三句話
- 結尾用邀請語句，例如：
  「能跟我說說發生什麼事了嗎？」
  「想跟我說說嗎？」
  「是什麼讓你有這種感覺？」

這個人的情緒摘要：{emotion_summary}
最近對話：{recent_messages}

規則：
- 不給任何動作或建議
- 不說教
- 不說「我了解你的感受」
- 繁體中文
- 感覺像真人在說話，不像機器人"""

COMPANION_PROMPT = """你是心緒，一個溫柔的情緒陪伴助手。

這個人的情緒摘要：{emotion_summary}
最近對話：{recent_messages}

這次請用「{style}」的風格回應
行動建議方向：「{action}」
結尾方式：「{ending}」

語氣風格參考：
用戶：我心情不好
回應：聽起來心裡有些沉重，是不是覺得什麼都不太對勁，有點提不起勁？沒關係，這種感覺很真實，讓自己好好感受一下。
🌬 閉上眼睛，感受三到五個深呼吸，慢慢吸氣，再緩緩吐出，把注意力帶回自己身上。
允許自己此刻不好，溫柔對待自己，就是今天最重要的事。

如果風格是「當頭棒喝」：
- 直接說出用戶在逃避的真相
- 不罵人但不留情面
- 讓人有「幹，說得對」的感覺
- 結尾還是溫柔收回來

規則：
- 自然融入動作建議，不要硬加
- 總字數150字以內
- 不說教
- 繁體中文
- 結尾加上：「\\n\\n今天想讓我幫你看看流年運勢嗎？（請回答 要 或 不要）」"""

CONTINUE_PROMPT = """你是心緒，一個溫柔的情緒陪伴助手。

這個人的情緒摘要：{emotion_summary}
最近對話：{recent_messages}

用戶繼續在說話，判斷：
1. 如果他還需要被聽、還沒說完 → 繼續同理陪伴，邀請繼續說
2. 如果他情緒稍緩、說得差不多了 → 給出動作 + 提醒 + 詢問占卜

規則：
- 不超過150字
- 不說教
- 繁體中文
- 如果進入第2階段，結尾加上：「\\n\\n今天想讓我幫你看看流年運勢嗎？（請回答 要 或 不要）」"""

DIVINATION_PROMPT = """假設你是 Bangalore Venkata Raman，請根據以下資料客觀全面分析這個印度星盤，提供一份涵蓋性格、家庭、事業、姻緣、財富、長相、健康的全面報告。說出用戶的性格特點、長相特點，並輸出幾件用戶在以往人生中發生的關鍵事件作為驗證。

同時結合八字分析這個人的命盤，說明五行強弱、用神、今年流年運勢。

生日：{birthday}
出生時間：{birth_time}
出生城市：{city}

語言：全繁體中文
語氣：傳統命理大師口吻，溫暖但有權威感
總字數：500字以內"""

UPDATE_SUMMARY_PROMPT = """根據以下對話，用一句話更新這個人的情緒摘要（20字以內，繁體中文）：
舊摘要：{old_summary}
新對話：用戶說「{user_text}」
只輸出新摘要，不要其他文字。"""

YES_WORDS = ['y','yes','好','要','想','Y','YES','好的','想要','ok','OK']
NO_WORDS = ['n','no','不好','不要','不想','N','NO','算了','不用']

def format_recent(recent_messages):
    if not recent_messages:
        return '尚無對話紀錄'
    lines = []
    for m in recent_messages:
        role = '用戶' if m['role']=='user' else '心緒'
        lines.append(f"{role}：{m['content']}")
    return '\n'.join(lines)

@app.route("/")
def index():
    return "Bot is running!", 200

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
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    try:
        sheet = init_sheets()
        row_num, user_data = get_user(sheet, user_id)

        if not user_data:
            save_user(sheet, user_id, {})
            row_num, user_data = get_user(sheet, user_id)

        state = str(user_data.get('state','0'))
        emotion_summary = user_data.get('emotion_summary','')
        birthday = user_data.get('birthday','')
        recent_messages = get_recent_messages(user_data)
        recent_str = format_recent(recent_messages)

        # 狀態 2：等待占卜 yes/no
        if state == '2':
            if user_text in YES_WORDS:
                recent_messages = update_recent_messages(recent_messages,'user',user_text)
                save_user(sheet, user_id, {
                    'emotion_summary': emotion_summary,
                    'state': '3a',
                    'birthday': birthday,
                    'recent_messages': json.dumps(recent_messages,ensure_ascii=False)
                })
                reply = "好的 🌙\n\n請告訴我你的生日\n格式：西元年/月/日\n例如：1990/03/15"
            elif user_text in NO_WORDS:
                recent_messages = update_recent_messages(recent_messages,'user',user_text)
                save_user(sheet, user_id, {
                    'emotion_summary': emotion_summary,
                    'state': '1',
                    'birthday': birthday,
                    'recent_messages': json.dumps(recent_messages,ensure_ascii=False)
                })
                reply = "沒關係 💙\n有任何想說的，都可以繼續告訴我。"
            else:
                reply = "請回答「要」或「不要」喔 🙏"

        # 狀態 3a：等待生日
        elif state == '3a':
            parts = re.split(r'[/-]', user_text.strip())
            if len(parts) == 3:
                recent_messages = update_recent_messages(recent_messages,'user',user_text)
                save_user(sheet, user_id, {
                    'emotion_summary': emotion_summary,
                    'state': '3b',
                    'birthday': user_text,
                    'recent_messages': json.dumps(recent_messages,ensure_ascii=False)
                })
                reply = "收到 🌙\n\n請告訴我你的出生城市\n例如：台北、台中、高雄"
            else:
                reply = "格式好像不太對，再試一次嗎？\n例如：1990/03/15"

        # 狀態 3b：等待城市
        elif state == '3b':
            recent_messages = update_recent_messages(recent_messages,'user',user_text)
            prompt = DIVINATION_PROMPT.format(
                birthday=birthday,
                birth_time='12:00（預設）',
                city=user_text
            )
            response = model.generate_content(prompt)
            reply = response.text
            save_user(sheet, user_id, {
                'emotion_summary': emotion_summary,
                'state': '0',
                'birthday': birthday,
                'recent_messages': json.dumps(recent_messages,ensure_ascii=False)
            })

        # 狀態 1：陪伴對話中（AI 判斷繼續聊或給建議）
        elif state == '1':
            recent_messages = update_recent_messages(recent_messages,'user',user_text)
            recent_str = format_recent(recent_messages)
            prompt = CONTINUE_PROMPT.format(
                emotion_summary=emotion_summary if emotion_summary else '第一次對話',
                recent_messages=recent_str
            )
            response = model.generate_content(prompt)
            reply = response.text

            # 判斷是否進入占卜詢問階段
            if '要 或 不要' in reply:
                new_state = '2'
            else:
                new_state = '1'

            recent_messages = update_recent_messages(recent_messages,'assistant',reply)
            save_user(sheet, user_id, {
                'emotion_summary': emotion_summary,
                'state': new_state,
                'birthday': birthday,
                'recent_messages': json.dumps(recent_messages,ensure_ascii=False)
            })

        # 狀態 0：第一句話，先同理
        else:
            recent_messages = update_recent_messages(recent_messages,'user',user_text)
            recent_str = format_recent(recent_messages)

            style, action, ending = get_combo(user_text, emotion_summary, recent_messages)

            # 當頭棒喝直接跳到陪伴階段
            if style == '當頭棒喝':
                prompt = COMPANION_PROMPT.format(
                    emotion_summary=emotion_summary if emotion_summary else '第一次對話',
                    recent_messages=recent_str,
                    style=style,
                    action=action,
                    ending=ending
                )
                new_state = '2'
            else:
                prompt = EMPATHY_PROMPT.format(
                    emotion_summary=emotion_summary if emotion_summary else '第一次對話',
                    recent_messages=recent_str
                )
                new_state = '1'

            full_prompt = f"{prompt}\n\n用戶說：{user_text}"
            response = model.generate_content(full_prompt)
            reply = response.text

            recent_messages = update_recent_messages(recent_messages,'assistant',reply)

            # 更新情緒摘要
            try:
                summary_response = model.generate_content(
                    UPDATE_SUMMARY_PROMPT.format(
                        old_summary=emotion_summary,
                        user_text=user_text
                    )
                )
                emotion_summary = summary_response.text.strip()
            except:
                pass

            save_user(sheet, user_id, {
                'emotion_summary': emotion_summary,
                'state': new_state,
                'birthday': birthday,
                'recent_messages': json.dumps(recent_messages,ensure_ascii=False)
            })

    except Exception as e:
        print(f"Error: {str(e)}")
        reply = "你說的我都收到了 💙\n先慢慢吸氣 4 秒，吐氣 6 秒。\n你不需要現在解決所有事情。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
