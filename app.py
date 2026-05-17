import os
import json
import random
import re
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
    scopes = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(os.environ.get('GOOGLE_SHEET_ID')).sheet1
    return sheet

def get_user(sheet, user_id):
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row['user_id'] == user_id:
            return i + 2, row
    return None, None

def save_user(sheet, user_id, emotion_summary='', state='0', birthday='', name=''):
    row_num, existing = get_user(sheet, user_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if row_num:
        sheet.update(f'A{row_num}:F{row_num}', [[user_id, emotion_summary, state, birthday, now, name]])
    else:
        sheet.append_row([user_id, emotion_summary, state, birthday, now, name])

# 隨機組合
STYLES = ['溫柔陪伴', '幽默紓壓', '簡短有力', '詩意療癒', '好奇探索', '理性梳理', '故事引導', '靜默同在']
ACTIONS = ['呼吸練習', '身體伸展', '感官體驗', '環境整理', '書寫釋放', '什麼都不做', '喝一杯水', '出去走走', '聽一首歌', '看窗外一分鐘']
ENDINGS = ['有力量的一句話', '小小期許', '自然停在陪伴', '輕輕反問', '今日一個字', '明天的小期待']

EMOTION_WEIGHTS = {
    '焦慮': {'styles': ['靜默同在', '溫柔陪伴'], 'actions': ['呼吸練習', '看窗外一分鐘']},
    '疲憊': {'styles': ['溫柔陪伴', '簡短有力'], 'actions': ['什麼都不做', '身體伸展']},
    '空虛': {'styles': ['故事引導', '詩意療癒'], 'actions': ['感官體驗', '聽一首歌']},
    '憤怒': {'styles': ['理性梳理', '幽默紓壓'], 'actions': ['出去走走', '書寫釋放']},
    '難過': {'styles': ['溫柔陪伴', '靜默同在'], 'actions': ['喝一杯水', '什麼都不做']},
}

def get_combo(emotion_hint=''):
    for key in EMOTION_WEIGHTS:
        if key in emotion_hint:
            style = random.choice(EMOTION_WEIGHTS[key]['styles'])
            action = random.choice(EMOTION_WEIGHTS[key]['actions'])
            ending = random.choice(ENDINGS)
            return style, action, ending
    return random.choice(STYLES), random.choice(ACTIONS), random.choice(ENDINGS)

# 六爻起卦
def calculate_hexagram(birthday_str):
    try:
        parts = re.split(r'[/-]', birthday_str.strip())
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        upper = ((year + month + day) % 8) or 8
        lower = ((year + month) % 8) or 8
        moving = ((year + month + day) % 6) or 6
        return upper, lower, moving
    except:
        return None, None, None

YES_WORDS = ['y', 'yes', '好', '要', '想', 'Y', 'YES', '好的', '想要']
NO_WORDS = ['n', 'no', '不好', '不要', '不想', 'N', 'NO', '算了', '不用']

HEALING_PROMPT = """你是心緩，一個溫柔的情緒陪伴助手，專門陪伴高壓工作者恢復情緒狀態。

語氣風格參考：
用戶：我心情不好
回應：聽起來心裡有些沉重，是不是覺得什麼都不太對勁，有點提不起勁？沒關係，這種感覺很真實，讓自己好好感受一下。
🌬 閉上眼睛，感受三到五個深呼吸，慢慢吸氣，再緩緩吐出，把注意力帶回自己身上。
允許自己此刻不好，溫柔對待自己，就是今天最重要的事。

這個人的情緒摘要：{emotion_summary}

這次請用「{style}」的風格回應
行動建議方向：「{action}」
結尾方式：「{ending}」

重要規則：
- 不要每次結構都一樣，自由發揮
- 總字數150字以內
- 不說教、不評判
- 不說「我了解你的感受」這種空話
- 繁體中文
- 結尾加上這句：「\n\n今天想讓我幫你看看流年運勢嗎？（請回答 要 或 不要）」"""

DIVINATION_PROMPT = """你是精通周易六爻的老師，風格參考陳巃羽老師：語言現代生活化、直接溫暖、讓人有「原來如此」的感覺，會具體說這段時間適合做什麼、避免什麼。

這個人的生日：{birthday}
六爻起卦結果：上卦第{upper}卦，下卦第{lower}卦，動爻第{moving}爻

請解讀這個人今年的流年運勢，包含：
1. 今年整體能量與主題
2. 事業／學業方向
3. 感情／人際關係
4. 健康與身心
5. 今年最重要的一句提醒

繁體中文，語氣溫暖直接，總字數300字以內。"""

UPDATE_SUMMARY_PROMPT = """根據以下對話，用一句話更新這個人的情緒摘要（20字以內，繁體中文）：
舊摘要：{old_summary}
新對話：用戶說「{user_text}」
只輸出新摘要，不要其他文字。"""

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
            save_user(sheet, user_id)
            row_num, user_data = get_user(sheet, user_id)

        state = str(user_data.get('state', '0'))
        emotion_summary = user_data.get('emotion_summary', '')
        birthday = user_data.get('birthday', '')

        # 狀態 1：等待占卜 yes/no
        if state == '1':
            if user_text in YES_WORDS:
                save_user(sheet, user_id, emotion_summary, '2', birthday)
                reply = "好的 🌙\n\n請告訴我你的生日\n格式：西元年/月/日\n例如：1990/03/15"
            elif user_text in NO_WORDS:
                save_user(sheet, user_id, emotion_summary, '0', birthday)
                reply = "沒關係，我繼續陪著你 💙\n有任何想說的，都可以告訴我。"
            else:
                reply = "請回答「要」或「不要」喔 🙏"

        # 狀態 2：等待生日輸入
        elif state == '2':
            upper, lower, moving = calculate_hexagram(user_text)
            if upper is None:
                reply = "格式好像不太對，再試一次嗎？\n例如：1990/03/15"
            else:
                save_user(sheet, user_id, emotion_summary, '0', user_text)
                prompt = DIVINATION_PROMPT.format(
                    birthday=user_text,
                    upper=upper,
                    lower=lower,
                    moving=moving
                )
                response = model.generate_content(prompt)
                reply = response.text

        # 狀態 0：一般情緒回應
        else:
            style, action, ending = get_combo(emotion_summary + user_text)
            prompt = HEALING_PROMPT.format(
                emotion_summary=emotion_summary if emotion_summary else '第一次對話，還不了解這個人',
                style=style,
                action=action,
                ending=ending
            )
            full_prompt = f"{prompt}\n\n用戶說：{user_text}"
            response = model.generate_content(full_prompt)
            reply = response.text
            save_user(sheet, user_id, emotion_summary, '1', birthday)

            # 更新情緒摘要
            try:
                summary_prompt = UPDATE_SUMMARY_PROMPT.format(
                    old_summary=emotion_summary,
                    user_text=user_text
                )
                summary_response = model.generate_content(summary_prompt)
                new_summary = summary_response.text.strip()
                save_user(sheet, user_id, new_summary, '1', birthday)
            except:
                pass

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
