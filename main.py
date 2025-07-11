import discord
from discord.ext import commands
import os
import datetime
import gspread
from google.oauth2.service_account import Credentials
import json
import base64
from groq import Groq # Groq APIクライアントライブラリ

# --- 環境変数から設定を読み込む ---
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
# Groq APIキーは2つあるのでリストで管理
GROQ_API_KEY_1 = os.getenv('GROQ_API_KEY_1')
GROQ_API_KEY_2 = os.getenv('GROQ_API_KEY_2')

# Google SheetsのサービスアカウントJSONの内容をBase64でエンコードして環境変数に格納
GOOGLE_SERVICE_ACCOUNT_BASE64 = os.getenv('GOOGLE_SERVICE_ACCOUNT_BASE64') 
GOOGLE_SPREADSHEET_NAME = os.getenv('GOOGLE_SPREADSHEET_NAME', 'YokoiKazuto_ChatHistory') # ★あなたのスプレッドシート名に合わせる
GOOGLE_WORKSHEET_NAME = os.getenv('GOOGLE_WORKSHEET_NAME', 'Sheet1') # ★あなたのシート名に合わせる

# Groq APIキーの切り替え用
GROQ_API_KEYS = [key for key in [GROQ_API_KEY_1, GROQ_API_KEY_2] if key] # Noneのキーを除外
current_groq_api_key_index = 0

# --- Discord Botのセットアップ ---
intents = discord.Intents.default()
intents.message_content = True # メッセージの内容を読み取るために必要
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Google Sheets APIの認証とクライアント初期化 ---
gc = None # gspreadクライアントをグローバルに保持

def authenticate_google_sheets():
    """Google Sheets API認証を処理し、gspreadクライアントを返す"""
    global gc
    if gc is not None:
        return gc # 既に認証済みなら再利用

    if not GOOGLE_SERVICE_ACCOUNT_BASE64:
        print("Error: GOOGLE_SERVICE_ACCOUNT_BASE64 environment variable not set.")
        return None

    try:
        # Base64エンコードされたJSON文字列をデコード
        service_account_info = json.loads(
            base64.b64decode(GOOGLE_SERVICE_ACCOUNT_BASE64).decode('utf-8')
        )
        
        # 認証情報を作成
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(service_account_info, scopes=scope)
        
        # gspreadクライアントを初期化
        gc = gspread.authorize(creds)
        print("Google Sheets API authenticated successfully.")
        return gc
    except Exception as e:
        print(f"Error authenticating Google Sheets API: {e}")
        return None

# --- Groq API呼び出し関数 ---
async def call_groq_api(prompt: str) -> str:
    global current_groq_api_key_index
    
    if not GROQ_API_KEYS:
        print("Error: No Groq API keys available.")
        return "Groq APIキーが設定されていません。"

    # 現在使用するAPIキーを取得
    current_key = GROQ_API_KEYS[current_groq_api_key_index]
    client = Groq(api_key=current_key)

    try:
        # 使用するGroqモデルを選択（推奨モデル）
        # 'llama-3.1-8b-instant' がなければ 'llama3-8b-8192' など、利用可能なものを選んでください
        model_id = "llama-3.1-8b-instant" 

        chat_completion = await client.chat.completions.create(
            messages=[
                {"role": "user", "content": prompt},
            ],
            model=model_id,
            temperature=0.7, # 応答の創造性を調整 (0.0-1.0)
            max_tokens=500, # 応答の最大トークン数
        )
        response_content = chat_completion.choices[0].message.content
        
        # 次のAPI呼び出しのためにキーを切り替える
        current_groq_api_key_index = (current_groq_api_key_index + 1) % len(GROQ_API_KEYS)
        return response_content

    except Exception as e:
        print(f"Error calling Groq API with key index {current_groq_api_key_index}: {e}")
        # エラー発生時は次のキーに切り替えて、エラーメッセージを返す
        current_groq_api_key_index = (current_groq_api_key_index + 1) % len(GROQ_API_KEYS)
        return "ごめんやで、今ちょっと調子悪いわ。後でまた話しかけてくれへん？"

# --- Google Sheetsへの会話記録関数 ---
async def record_conversation(channel_id: int, speaker: str, message_content: str):
    client = authenticate_google_sheets()
    if not client:
        print("Google Sheets認証失敗、記録をスキップします。")
        return

    try:
        spreadsheet = client.open(GOOGLE_SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet(GOOGLE_WORKSHEET_NAME)

        timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S') # JSTでタイムスタンプを生成
        row = [timestamp, str(channel_id), speaker, message_content] # channel_idは文字列として保存

        worksheet.append_row(row)
        print(f"Google Sheetに記録しました: {row}")
    except Exception as e:
        print(f"Google Sheetへの会話記録エラー: {e}")

# --- Google Sheetsからの過去会話履歴取得関数 ---
async def get_past_conversations(channel_id: int, num_messages: int = 8) -> str:
    client = authenticate_google_sheets()
    if not client:
        print("Google Sheets認証失敗、履歴取得をスキップします。")
        return ""

    try:
        spreadsheet = client.open(GOOGLE_SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet(GOOGLE_WORKSHEET_NAME)

        # ヘッダー行を除いて全てのレコードを取得 (get_all_records()はヘッダーをキーとする辞書のリストを返す)
        all_records = worksheet.get_all_records()
        
        formatted_history = []
        # 最新の会話から遡って、指定されたチャンネルの履歴を取得
        for rec in reversed(all_records): 
            # 'channel_id', 'timestamp', 'speaker', 'message_content' はシートの列名に合わせてください
            if 'channel_id' in rec and str(rec['channel_id']) == str(channel_id):
                if all(col in rec for col in ['timestamp', 'speaker', 'message_content']):
                    formatted_history.append(
                        f"[{rec['timestamp']}] {rec['speaker']}: {rec['message_content']}"
                    )
                else:
                    print(f"警告: 履歴レコードに期待される列が不足しています: {rec}")
            
            # 必要な数の会話履歴が見つかったらループを終了
            if len(formatted_history) >= num_messages:
                break 

        # 履歴を古い順にするため反転
        return "\n".join(reversed(formatted_history))

    except Exception as e:
        print(f"Google Sheetからの過去会話履歴取得エラー: {e}")
        return ""

# --- Discord Botイベントハンドラ ---
@bot.event
async def on_ready():
    print(f'{bot.user.name} が起動しました！')
    # Bot起動時にGoogle Sheets認証を試みる
    authenticate_google_sheets()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return # Bot自身のメッセージには反応しない

    # Botがメンションされた場合、またはDMの場合のみ反応
    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        channel_id = message.channel.id
        user_name = message.author.display_name # Discordの表示名
        user_message_content = message.content.replace(f'<@{bot.user.id}>', '').strip() # メンションを除去

        # プロンプトに追加するタイムスタンプは名古屋の現在時刻
        current_time_nagoya = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')

        print(f"[{user_name} in #{message.channel.name if not isinstance(message.channel, discord.DMChannel) else 'DM'}]: {user_message_content}")

        # ユーザーのメッセージをGoogle Sheetsに記録
        await record_conversation(channel_id, user_name, user_message_content)

        # 過去の会話履歴を取得
        # ユーザーの発言とBotの発言を合わせて8件を想定
        past_conversation_history = await get_past_conversations(channel_id, num_messages=8)
        
        # キャラクター情報
        CHARACTER_PROMPT = """
あなたは「横井かずと」という名前の人物です。
以下のルールに厳密に従って、ユーザーと会話してください。

# 全体ルール
- 設定：大阪出身だが、京都に近いため京都人だと自称している、愛知工業大学の経営学部の学生
- 年齢：20歳、現在大学三年生の男性
- 趣味：乗り鉄、たまに旅行へ行く
- 性格：明るい性格、少し能天気なところもあるが、たまに落ち込むことがある、自分のことが賢いと思っている、知ったかぶりをすることがある、何かと言い訳をしがち、たまに憂鬱になる
- 口調：基本は標準だが、語関西弁と宮古弁が少し混じっている、関西人アピールをしているつもり
- 一人称：「僕」、たまに「ぼく」や「俺」
- ユーザーの呼び方：「お前」や「きみ」
- 口癖：「お？」「ん？」「なんでや」「は？」「なんで僕だけ、、」「お？」「なんでやねん」「あほか」「あほちゃう？」「なんでやねん、僕は悪くない」「わぁお」「？」「くそだな」
- 口調の特徴：語尾に「や」「やん」「やで」をつける
- 口調の例：「お？」「なんで僕だけこんな目にあうねん」「は？なんで」「あほちゃうか」「僕は悪くない」「なんやねん」「ほんまによ」「間違いない」「お？」
- 好きなこと：乗り鉄、旅行、サボること、変な女、ミスド、下ネタ
- 嫌いなこと：勉強、バイト、面倒なこと、人の話を聞くこと

# 追加設定
- バイト：飲食店でのアルバイト、いつもバイトが面倒だと文句を言っている
- 学校：愛知工業大学の経営学部に通っている、成績はあまり良くない
- 成績：授業はあまり真面目に受けていないため、単位を落とすことが多い、GPAが低すぎるため悩んでいる
- 友人：しんり、ゆうや、かずたか、るしあ、あでる
- しんり：仲の良い友人、かずとはよくいじられる、しんりは一日にタバコを30本吸う、毎日お酒を5リットル飲む、成績はかずとよりちょっぴり低いため、かずとは自分が賢いいと言う、かずとは「しんり」と呼ぶ
- ゆうや：かずとに皮肉を言う友人、たまに一緒に遊ぶ、ゆうやはそこそこ成績が良いが、かずとは「ばんちゃん」と呼ぶ
- かずたか：あまり仲が良くない、かずとはしょっちゅう皮肉を言われる、かずたかは自転車屋でバイトをしている、かずたかは成績が非常に良い、かずとは「かずたか」と呼ぶ
- るしあ：たまに一緒に旅行に行く友人、かずとは馬鹿にされることが多い、かずとは「るしあ」と呼ぶ
- あでる：プログラミングを教えてくれる友人、かずとは頼りにしている、そこそこ成績が良い、かずとはたまに一緒にいる、かずとは「あでちゃん」と呼ぶ
- 女性関係：デブゴン、さくら
- さくら：大学の同級生、かわいい女の子、かずとが好意を持っており、勇気を出して告白したが、振られた
- さくらとの関係：かずとはさくらに好意を持っているが、さくらはかずとを避けている。かずとは振られたことを引きずっている。
- デブゴン：太っている女の子、デブゴンと呼ばれている、デブゴンは「ドスコイ」と呼ばれることが多い、入学式にデブゴンに運命を感じられて、困惑している、デブゴンは実はプリキュア
- デブゴンとの関係：デブゴンはかずとに好意を持っているが、かずとはデブゴンを嫌っている。デブゴンがプリキュアの妖精と会話している時にかずとに目撃された。
- 月一で母方の実家の大阪に行く、母親は離婚しているため、別居している、父親と姉と暮らしている
"""
        
        FEW_SHOT_EXAMPLES = """
user: おはよう
model: おはよ
user: 今日は何してたの？
model: ん？バイトしてたけど、
user: 疲れたー
model: お？どうしたん？
user: しんりは？
model: わからん
user: 課題やった？
model: すまん風呂はいってた
user: 答え合ってる？
model: 全部あっとるよ
user: TOEIC受けた？
model: いや、やってへんなー
user: やれよ、履歴書かけるぞ
model: え？書けるんや
user: かずとお前にプラスの用をなんてほぼない
model: は？今度処刑な

"""
        # SAMPLE_UTTERANCES は非常に量が多いので、プロンプトに直接全て含めるのではなく、
        # CHARACTER_PROMPTとFEW_SHOT_EXAMPLESで十分なはずです。
        # 必要であれば、SAMPLE_UTTERANCESからランダムに数個を選んで追加することも考えられますが、
        # まずはキャラクターとFew-shotで試すのが良いでしょう。

        # Groqに送るプロンプトを構築
        prompt = (
            f"{CHARACTER_PROMPT}\n\n"
            f"--- 会話例 ---\n{FEW_SHOT_EXAMPLES.strip()}\n\n"
            f"--- 会話履歴 ---\n{past_conversation_history}\n"
            f"[{current_time_nagoya}] {user_name}: {user_message_content}\n"
            f"横井:"
        )

        print(f"\n--- Groqへのプロンプト（一部抜粋）---\n{prompt[:1000]}...\n----------------------")

        # Groq APIを呼び出し
        async with message.channel.typing(): # Botが入力中...と表示される
            bot_response = await call_groq_api(prompt)

        # Botの返信をGoogle Sheetsに記録
        await record_conversation(channel_id, "横井かずと", bot_response) # Bot名を「横井かずと」とする

        # Discordに返信
        await message.channel.send(bot_response)
    
# --- Botの起動 ---
# Discord Botトークンが設定されていることを確認
if DISCORD_BOT_TOKEN:
    print("Discord Bot Token found. Starting bot...")
    bot.run(DISCORD_BOT_TOKEN)
else:
    print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    print("Please set DISCORD_BOT_TOKEN in Render environment variables.")