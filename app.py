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
from linebot.models import (MessageEvent, TextMessage, TextSendMessage, FollowEvent)

app = Flask(__name__)
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

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
    row = [
        user_id,
        data.get('emotion_summary',''),
        data.get('state','0'),
        data.get('birthday',''),
        now,
        data.get('name',''),
        data.get('recent_messages',''),
        data.get('onboarded','0')
    ]
    if row_num:
        sheet.update(f'A{row_num}:H{row_num}', [row])
    else:
        sheet.append_row(row)

def get_recent_messages(user_data):
    try:
        msgs = user_data.get('recent_messages','')
        if not msgs:
            return []
        return json.loads(msgs)
    except:
        return []

def update_messages(messages, role, content):
    messages.append({'role': role, 'content': content})
    return messages[-5:]

def format_recent(messages):
    if not messages:
        return '尚無對話紀錄'
    lines = []
    for m in messages:
        role = '用戶' if m['role']=='user' else '心緒'
        lines.append(f"{role}：{m['content']}")
    return '\n'.join(lines)

# Prompts
QUOTE_PROMPT = """你是心緒，根據用戶說的話，選一句最適合的名言、金句或電影台詞送給他。

規則：
- 只輸出一句話加來源，不要其他文字
- 格式：「金句內容」\n— 來源（作者或電影名）
- 金句要精準打中用戶的感受
- 可以是中文或翻譯過來的外文金句
- 不要自己創作，要是真實存在的句子
- 繁體中文

用戶說：{user_text}"""

MONTHLY_FORTUNE_PROMPT = """假設你是 Bangalore Venkata Raman，根據用戶的八字和印度星盤，給出這個月的運勢建議。

生日：{birthday}
本月：{current_month}

規則：
- 只說這個月，不說整年
- 最多3句話
- 語氣像命理大師，溫暖有權威感
- 繁體中文
- 不要說「根據你的星盤」這種開場白，直接說建議"""

ONBOARD_QUOTE_PROMPT = """你是心緒，根據用戶說的煩惱，選一句最適合的名言、金句或電影台詞送給他，再加上這個月的運勢建議。

生日：{birthday}
本月：{current_month}
用戶的煩惱：{concern}

輸出格式（嚴格遵守，不要加其他文字）：
「金句內容」
— 來源

【本月提醒】
運勢建議第一句
運勢建議第二句
運勢建議第三句"""

COMPANION_PROMPT = """你是心緒，一個溫柔的情緒陪伴助手。

這個人的情緒摘要：{emotion_summary}
最近對話：{recent_messages}

規則：
- 先給一個小動作幫助恢復（30秒到3分鐘內能完成，用emoji開頭）
- 再給一句有力量的話
- 最後問：「今天想讓我幫你看看本月運勢嗎？（請回答 要 或 不要）」
- 總字數100字以內
- 繁體中文
- 不說教"""

CONTINUE_PROMPT = """你是心緒，一個溫柔的情緒陪伴助手。

這個人的情緒摘要：{emotion_summary}
最近對話：{recent_messages}

用戶繼續說話，判斷：
1. 如果他還沒說完、情緒還在 → 繼續陪伴，邀請繼續說（一句話就好）
2. 如果情緒稍緩、說得差不多了 → 給小動作 + 一句話 + 詢問本月運勢

規則：
- 總字數80字以內
- 繁體中文
- 如果進入第2階段結尾加：「\\n\\n今天想讓我幫你看看本月運勢嗎？（請回答 要 或 不要）」"""

UPDATE_SUMMARY_PROMPT = """用一句話更新這個人的情緒摘要（20字以內，繁體中文）：
舊摘要：{old_summary}
新對話：{user_text}
只輸出新摘要。"""

YES_WORDS = ['y','yes','好','要','想','Y','YES','好的','想要','ok','OK']
NO_WORDS = ['n','no','不好','不要','不想','N','NO','算了','不用']

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

# 加入好友事件
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    try:
        sheet = init_sheets()
        save_user(sheet, user_id, {'state': 'ob1', 'onboarded': '0'})
    except Exception as e:
        print(f"Follow error: {e}")
    reply = "你好，這裡是心緩 🌙\n\n在我們開始之前，想多認識你一點\n\n請告訴我你的生日\n格式：西元年/月/日\n例如：1990/03/15"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()
    current_month = datetime.now().strftime('%Y年%m月')

    try:
        sheet = init_sheets()
        row_num, user_data = get_user(sheet, user_id)

        if not user_data:
            save_user(sheet, user_id, {'state': 'ob1', 'onboarded': '0'})
            row_num, user_data = get_user(sheet, user_id)

        state = str(user_data.get('state','0'))
        emotion_summary = user_data.get('emotion_summary','')
        birthday = user_data.get('birthday','')
        onboarded = str(user_data.get('onboarded','0'))
        recent_messages = get_recent_messages(user_data)

        # 入場流程 ob1：等待生日
        if state == 'ob1':
            parts = re.split(r'[/-]', user_text.strip())
            if len(parts) == 3:
                save_user(sheet, user_id, {
                    'state': 'ob2',
                    'birthday': user_text,
                    'onboarded': '0',
                    'emotion_summary': emotion_summary,
                    'recent_messages': json.dumps(recent_messages, ensure_ascii=False)
                })
                reply = "謝謝你 💙\n\n最近有什麼讓你感到困擾或沉重的事嗎？"
            else:
                reply = "格式好像不太對，再試一次嗎？\n例如：1990/03/15"

        # 入場流程 ob2：等待煩惱
        elif state == 'ob2':
            prompt = ONBOARD_QUOTE_PROMPT.format(
                birthday=birthday,
                current_month=current_month,
                concern=user_text
            )
            response = model.generate_content(prompt)
            reply = response.text

            recent_messages = update_messages(recent_messages, 'user', user_text)
            recent_messages = update_messages(recent_messages, 'assistant', reply)

            try:
                summary_response = model.generate_content(
                    UPDATE_SUMMARY_PROMPT.format(old_summary='', user_text=user_text)
                )
                emotion_summary = summary_response.text.strip()
            except:
                emotion_summary = ''

            save_user(sheet, user_id, {
                'state': '0',
                'birthday': birthday,
                'onboarded': '1',
                'emotion_summary': emotion_summary,
                'recent_messages': json.dumps(recent_messages, ensure_ascii=False)
            })

        # 等待運勢 yes/no
        elif state == '2':
            if user_text in YES_WORDS:
                prompt = MONTHLY_FORTUNE_PROMPT.format(
                    birthday=birthday,
                    current_month=current_month
                )
                response = model.generate_content(prompt)
                reply = response.text
                recent_messages = update_messages(recent_messages, 'assistant', reply)
                save_user(sheet, user_id, {
                    'state': '0',
                    'birthday': birthday,
                    'onboarded': '1',
                    'emotion_summary': emotion_summary,
                    'recent_messages': json.dumps(recent_messages, ensure_ascii=False)
                })
            elif user_text in NO_WORDS:
                reply = "沒關係 💙\n有任何想說的，都可以繼續告訴我。"
                save_user(sheet, user_id, {
                    'state': '0',
                    'birthday': birthday,
                    'onboarded': '1',
                    'emotion_summary': emotion_summary,
                    'recent_messages': json.dumps(recent_messages, ensure_ascii=False)
                })
            else:
                reply = "請回答「要」或「不要」喔 🙏"

        # 狀態 1：陪伴中，AI 判斷繼續或給建議
        elif state == '1':
            recent_messages = update_messages(recent_messages, 'user', user_text)
            recent_str = format_recent(recent_messages)
            prompt = f"{CONTINUE_PROMPT.format(emotion_summary=emotion_summary or '第一次對話', recent_messages=recent_str)}\n\n用戶說：{user_text}"
            response = model.generate_content(prompt)
            reply = response.text

            new_state = '2' if '要 或 不要' in reply else '1'
            recent_messages = update_messages(recent_messages, 'assistant', reply)

            save_user(sheet, user_id, {
                'state': new_state,
                'birthday': birthday,
                'onboarded': '1',
                'emotion_summary': emotion_summary,
                'recent_messages': json.dumps(recent_messages, ensure_ascii=False)
            })

        # 狀態 0：新的一輪對話，給金句
        else:
            recent_messages = update_messages(recent_messages, 'user', user_text)

            prompt = QUOTE_PROMPT.format(user_text=user_text)
            response = model.generate_content(prompt)
            quote_reply = response.text.strip()

            invite = "\n\n想跟我說說發生什麼事了嗎？"
            reply = quote_reply + invite

            recent_messages = update_messages(recent_messages, 'assistant', reply)

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
                'state': '1',
                'birthday': birthday,
                'onboarded': '1',
                'emotion_summary': emotion_summary,
                'recent_messages': json.dumps(recent_messages, ensure_ascii=False)
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
