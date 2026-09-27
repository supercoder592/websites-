"""意圖路由：所有功能都在同一個聊天室，由這裡判斷使用者想做什麼。

先用規則快速判斷（不耗 CPU），只有在規則互相衝突時才請本機 LLM 做最後裁決。
"""
import re

INTENTS = ("chat", "code", "image", "video", "music", "model3d", "search")

LABELS = {
    "chat": "對話", "code": "程式", "image": "圖像", "video": "影片",
    "music": "音樂", "model3d": "3D 模型", "search": "素材搜尋",
}

COMMANDS = {
    "畫": "image", "圖": "image", "圖片": "image", "圖像": "image", "image": "image", "img": "image", "draw": "image",
    "影片": "video", "動畫": "video", "視頻": "video", "video": "video",
    "音樂": "music", "歌": "music", "作曲": "music", "music": "music", "song": "music",
    "3d": "model3d", "3d模型": "model3d", "模型": "model3d", "建模": "model3d", "model": "model3d",
    "搜": "search", "搜尋": "search", "搜圖": "search", "找": "search", "search": "search", "find": "search",
    "程式": "code", "code": "code",
    "聊": "chat", "聊天": "chat", "對話": "chat", "chat": "chat",
}
CMD_ORDER = sorted(COMMANDS, key=len, reverse=True)   # 最長的先比對：/3d模型 不會被當成 /3d

_I = re.I
SEP = r"[^，。,.!?！？\n]"  # 同一句內
# 動詞與名詞之間的距離：一個英文字 / 數字算一格，避免「生成一支 10 秒的產品宣傳影片」超出範圍（各選項互不重疊，不會回溯爆炸）
GAP = r"(?:[A-Za-z0-9\-]+(?![A-Za-z0-9\-])|[^，。,.!?！？\nA-Za-z0-9\-])"
# 「影片標題」「歌曲的點子」「圖片的描述」要的是文字，不是作品
NOT_TEXT = r"(?![的之]?\s*(標題|點子|腳本|文案|句子|劇本|名稱|名字|建議|想法|大綱|描述|介紹|心得|清單|列表|網站|連結|企劃|提案|開頭|結尾|字幕|旁白))"
# 生成類動詞（中文 + 英文）
GEN_ZH = r"(生成|產生|製作|做|弄|創作|設計|來一|來個|來段|來首|來張|給我|畫|繪|寫|譜|建立|建|打造|渲染|合成|輸出|變成|轉成|改成)"
# 「你畫的圖」「生成的圖」「畫了一張」是在談論已完成的東西
NOT_DONE = r"(?![的得過了著])"
GEN_ZH_ACT = GEN_ZH + NOT_DONE
# draw / paint / sketch 的慣用語（draw the line / draw conclusions / draw attention / sketch out）不是要畫圖
EN_IDIOM = r"(?!\s+(the line|a line|conclusions?|attention|inspiration|a blank|out|up|on|upon|from|straws|near)\b)"
GEN_EN = r"\b(generate|create|make|render|compose|produce|design|build|give me|write|(draw|paint|sketch)" + EN_IDIOM + r")\b"

# 「怎麼畫」「如何生成」「想學做動畫」這類是在問方法，不是要我生成
HOWTO = re.compile(r"(怎麼|怎样|怎樣|如何|為什麼|为什么|教我|步驟|方法|原理|技巧|想學|要學|學做|學會|自學|"
                   r"學\s*(blender|maya|c4d|zbrush|3ds ?max|建模|作曲|編曲|剪輯|動畫)|\bhow (do|to|can)\b)", _I)
# 「你會畫畫嗎」這類是在問能力
ABILITY = re.compile(r"(你|妳|nova)" + SEP + r"{0,4}(會|能|可以|可不可以|能不能|有辦法)" + SEP + r"{0,10}(嗎|麼|？|\?)\s*$"
                     r"|\b(can|could) you\b" + SEP + r"{0,40}\?\s*$", _I)
# 「你可以幫我畫一隻龍嗎」「can you draw me a cat?」是客氣的請求，不是問能力
REQUEST = re.compile(r"(幫我|幫忙|替我|為我|給我)"
                     r"|\b(can|could|would) you\s+(please\s+)?\w+\s+(me\s+)?(an?|some|the|this|that|my|\d+)\b", _I)
# 推薦 / 是什麼 這類一定是資訊型；介紹 / 歷史 只有在沒有明確生成要求時才算
# 注意「比較大的」「有意思的」「新聞風格」是修飾語，不是在問問題
INFO_HARD = re.compile(r"(推薦|是什麼|是誰|誰是|什麼是|解釋|評價|差別|差異|(?<!有)意思|歌詞|歌單|有哪些|有什麼|哪[一些裡個首部款種]|要多久|多少錢|"
                       r"比較(?=" + SEP + r"{0,12}(和|跟|與|還是|哪|差|優缺))|"
                       r"\bwhat (is|are)\b|\bwhat's\b|\bexplain\b|\brecommend|\bdifference\b)", _I)
INFO_SOFT = re.compile(r"(介紹|歷史|心得|分析)", _I)
# 「作曲家 / 作曲者 / 作曲系 / 作曲軟體」「周杰倫作曲的」是名詞或已完成，不是要我作曲
COMPOSE_ACT = r"(作曲|編曲|譜曲)(?![者家人師系軟的得過了著])"
STRONG_REQ = re.compile(COMPOSE_ACT + r"|(生成|產生|創作)|(做|來|給我|設計|畫|繪|製作|寫|譜|要)" + NOT_DONE + SEP +
                        r"{0,3}(一|兩|幾|\d+)\s*[張幅首段支部個]")
# 資訊型句子裡仍然明確要求生成（「生成一段介紹公司的影片」）
GEN_VERB = re.compile(r"(生成|產生|製作|創作)|(做|弄|來)" + NOT_DONE + SEP + r"{0,3}(一|兩|\d+)\s*[張幅首段支部個]")
COMPOSE = re.compile(COMPOSE_ACT)
# 資訊型句子只有明確要求作曲才算音樂（「幫我作曲」「作曲一首」可以；「這首歌是誰作曲」不行）
MUSIC_GEN = re.compile(r"(生成|產生|創作)|(幫我|幫忙|替我|為我|給我|請你?|我要|想要)\s*" + COMPOSE_ACT +
                       "|" + COMPOSE_ACT + SEP + r"{0,3}(一|兩|幾|\d+)\s*[首段曲]")

TECH = (r"(python|javascript|typescript|\bjs\b|html|css|canvas|svg|matplotlib|pillow|opencv|pygame|p5\.?js|three\.?js|"
        r"unity|unreal|godot|react|vue|node\.?js|\bjava\b|c\+\+|c#|golang|\brust\b|\bsql\b|vba|c ?語言)")
# 「用 Python 畫…」「用 CSS 做動畫」「in react」一定是寫程式
USE_TECH = re.compile(r"(用|使用|透過|\bin\b|\busing\b)\s*" + TECH, _I)
CODE_WORDS = re.compile(
    r"(程式|程序|代碼|代码|原始碼|源碼|源代碼|函式|函數|(?<!影片)(?<!短片)(?<!廣告)(?<!拍攝)腳本|演算法|算法|正規表達式|除錯|報錯|編譯|"
    r"前端|後端|資料庫|爬蟲|flask|django|fastapi|hello world|\bgit\b|docker|\bbash\b|powershell|c ?語言|"
    r"ffmpeg|imagemagick|yt-dlp|命令列|終端機|批次檔|\bshell\b|"
    r"html|css|javascript|typescript|python|java\b|c\+\+|c#|golang|\brust\b|\bsql\b|regex|\bapi\b|\bbug\b|"
    r"debug|\bcode\b|coding|function|script|react|vue|node\.?js|three\.?js|\bjs\b|\bts\b|json|excel 公式|vba|"
    r"流程圖|架構圖|心智圖|甘特圖|\buml\b|er ?圖|類別圖|mermaid)", _I)
CODE_WEAK = re.compile(r"(網頁|網站)")   # 「推薦幾個學英文的網站」不是寫程式，只有不是資訊型問題時才算
CODE_BUILD = re.compile(r"(做|寫|開發|製作|設計|建|打造|弄)" + SEP + r"{0,16}(遊戲|app|應用|小工具|工具|計算機|網頁|網站|外掛|機器人|bot|"
                        r"播放器|編輯器|下載器|轉檔器|擴充功能)"
                        r"|\b(write|build|code|develop|make|create)\b" + SEP +
                        r"{0,30}\b(app|game|website|web ?page|landing page|tool|bot|program|script|extension|plugin|player)\b", _I)

# 「我找不到照片」「查詢圖書館」不是找素材
SEARCH = re.compile(
    r"(找(?!不到|不著|到了|回)|搜尋|搜索|搜|查|蒐集|收集|抓|下載)" + SEP +
    r"{0,20}(圖片|照片|素材|圖庫|參考圖|參考|桌布|壁紙|影片|視頻|影像|圖檔|icon|圖示|gif|動圖|圖(?![書表館])|"
    r"footage|stock|photos?|images?|pictures?|videos?|clips?|wallpapers?)"
    r"|(給我|我要|需要|想要)" + SEP + r"{0,14}(素材|圖庫)"
    # 授權用語（可商用 / 免版權 / CC0）＋素材，是要找現成的
    r"|(可商用|免版權|無版權|免費商用|\bcc0\b|royalty[- ]free|creative commons)" + SEP +
    r"{0,14}(素材|圖庫|圖片|照片|影片|視頻|圖(?![書表館])|footage|photos?|images?|pictures?|videos?|clips?)"
    r"(?!" + SEP + r"{0,6}(商用|授權|版權))"   # 「cc0 授權的照片可以商用嗎」是在問授權
    r"|\b(find|search|look for|get me|show me|collect|download)\b" + SEP +
    r"{0,40}\b(images?|pictures?|photos?|stock|footage|videos?|clips?|references?|wallpapers?|assets?|gifs?)\b", _I)
# 「查一下這張照片是在哪裡拍的」是在問某張特定的圖，不是上網找素材（除非要找類似的）
SELF_REF = re.compile(r"(這|那|此)(張|個|幅)?\s*(圖|照片|圖片|相片|影像)")
SIMILAR = re.compile(r"(類似|相似|同款|一樣|更多)")
SEARCH_VIDEO = re.compile(r"(影片|視頻|\bvideos?\b(?! ?games?)|\bfootage\b|\bclips?\b)", _I)
SEARCH_STILL = re.compile(r"(截圖|劇照|縮圖|\bscreenshots?\b|\bthumbnails?\b|\bstills?\b)", _I)

MODEL3D = re.compile(
    r"(3d|三維|立體)\s*(模型|物件|物體|建模|模型檔|資產|素材|雕塑|公仔|角色|道具)|建模|\bglb\b|\.obj\b|\bmesh\b|網格模型|low[ -]?poly|低多邊形"
    r"|\b3d (model|mesh|object|asset|sculpture)\b|\bfigurine\b"
    r"|" + GEN_ZH_ACT + SEP + r"{0,8}(3d|三維|立體)"
    # 「我需要一個可以 3D 列印的手機架模型」（「這個 3D 遊戲的模型好醜」沒有要求，不算）
    r"|(需要|想要|我要|給我|幫我)" + SEP + r"{0,12}(3d|三維|立體)" + SEP + r"{0,10}(模型|物件)"
    r"|(生成|產生|製作|做|弄|創作|設計|建立|打造)" + NOT_DONE + SEP + r"{0,10}(公仔|手辦)"   # 「畫一個公仔」是畫圖
    r"|" + GEN_EN + SEP + r"{0,30}\b3d\b", _I)

MUSIC_NOUN = (r"(音樂|歌曲|曲子|樂曲|純音樂|旋律|配樂|伴奏|背景音樂|bgm|beat|lofi|lo-fi|電音|交響|鋼琴曲|小調|音效|節奏|ost|主題曲|"
              r"(爵士|搖滾|古典|電子|流行|鄉村|管弦|國|民族|打擊)樂(?![團手隊器迷評界壇譜理觀意於趣園天])|演奏(?![會家者廳技])|"
              r"進行曲|搖籃曲|小夜曲|奏鳴曲|協奏曲|狂想曲|夜曲|舞曲|"
              r"(?<!詩)歌(?!詞|手|星|單|唱|劇|迷|名|廳))")
# 「來一首 jazz」「做一首搖籃曲」「再來一首」— 量詞「首」本身就代表歌（但「寫一首詩 / 俳句」是寫作）
MUSIC_MEASURE = (r"((生成|產生|製作|做|弄|創作|來|給我|譜)" + NOT_DONE + SEP + r"{0,3}(一|兩|幾|\d+)\s*首|來首)"
                 r"(?!" + SEP + r"{0,6}(詩|詞|絕句|律詩|新詩|打油詩|歌詞|俳句|haiku|對聯|順口溜))")
# 「song suggestions / music recommendations」要的是文字
MUSIC_EN = (r"\b(music|song|beat|track|melody|soundtrack|tune|jingle|bgm)s?\b"
            r"(?!\s+(suggestions?|recommendations?|recs?|ideas?|names?|titles?|lyrics|playlists?|lists?|tips|advice|theory|lessons?|class))")
MUSIC = re.compile(
    COMPOSE_ACT +
    r"|(" + GEN_ZH_ACT + r"|來點|來些|放點|放些)" + GAP + r"{0,12}" + MUSIC_NOUN + NOT_TEXT +
    # 「聽了一首」「聽過一首歌」「彈到一段」是在敘述，不是要我做
    r"|(?<![唱聽彈])(?<![唱聽彈][了過到])(一首|一段|一曲)" + GAP + r"{0,10}" + MUSIC_NOUN +
    r"|" + MUSIC_MEASURE +
    r"|" + GEN_EN + SEP + r"{0,30}" + MUSIC_EN +
    r"|\b(i[’']?d like|i want|i need)\s+(an?|some|another|\d+)\b" + SEP + r"{0,20}" + MUSIC_EN, _I)

VIDEO_NOUN = r"(影片|視頻|動畫|短片|(?<![a-z])mv(?![a-z])|動態影像|縮時|video|animation|clip|gif|動圖)"   # 「一支MV」中英相連也要抓到
VIDEO = re.compile(
    GEN_ZH_ACT + GAP + r"{0,12}" + VIDEO_NOUN + NOT_TEXT + r"|動起來|動態化|讓(它|這張|圖片?)" + SEP + r"{0,4}動"
    r"|" + GEN_EN + SEP + r"{0,30}\b(video|animation|clip|gif)\b|\banimate\b", _I)

# 注意「計畫」「動畫」「漫畫」「畫面」「畫家」「畫大餅」「畫出重點」不是要畫圖
DRAW_IDIOM = r"(?![個張]?大餅|重點|來的)"
DRAW_EXCL = (r"(?!面|家|展|風|質|素|廊|室|冊|報|法|筆|布|壇|派|框|餅|技|的|得|過|了|著|重點|線|押|中|裡|內|錯|完|好|作|具|師|上去|"
             r"一?個?大餅|出重點|出來的)")
# 畫 + 量詞 / 數字（畫一隻、畫3隻、畫兩隻）
DRAW_STRICT = (r"((?<![計规規企策刻動漫版字書油壁國筆])(畫|繪製|繪)(一|個|張|幅|出|隻|只|下|些|給|成|條|朵|座|位|名|顆|輛|棵|片|群|套|兩|幾|\d)"
               + DRAW_IDIOM + ")")
# 句首或「幫我」後面的「畫X」（畫皮卡丘、幫我畫台北101）
DRAW = (r"(" + DRAW_STRICT +
        r"|^\s*(請|麻煩)?\s*(你|幫我|幫忙|替我|給我)?\s*(再|重新)?\s*畫" + DRAW_EXCL +
        r"|(幫我|幫忙|替我|給我|請你|麻煩你)\s*(再|重新)?\s*畫" + DRAW_EXCL + ")")
DRAW_RE = re.compile(DRAW_STRICT, _I)   # 資訊型 / 寫作的例外只相信「畫 + 量詞」
IMAGE_NOUN = (r"(圖片|圖像|圖畫|照片|相片|插畫|插圖|海報|桌布|壁紙|頭像|logo|標誌|貼圖|封面|概念圖|圖案|繪畫|畫作|"
              r"寫真|肖像|風景圖|示意圖|美圖|一張圖|張圖|幅畫|的圖|ai圖|ai 圖|icon|圖示|圖)")
# 「來一張台北夜景」「我要一張…」「生成一張水彩畫」— 量詞「張/幅」本身就代表圖（但「一張票 / 清單 / 請假單」不是）
IMG_MEASURE = (r"((生成|產生|製作|做|弄|創作|設計|來|給我|要|繪|畫|輸出|渲染|生|generate|create|make|draw)" + NOT_DONE + SEP +
               r"{0,4}(一|兩|三|四|五|幾|\d+)\s*[張幅]|來[張幅])"
               r"(?!" + SEP + r"{0,15}(清單|名單|菜單|帳單|單子|列表|表格|表單|表(?!情|演|現)|票|紙|床|桌子|椅子|證|履歷|考卷|試卷|講義|"
               r"信用卡|悠遊卡|會員卡|光碟|唱片|投影片|簡報|支票|發票|收據|券|(?<![傳簡孤])單(?![純調色車身人獨])|書|ppt|word|excel))")
# 英文 draw / paint 只在句首或請求語後才算，且排除慣用語（draw the line / draw conclusions / sketch out / paint on …）
DRAW_EN = (r"(?:^\s*|[，。,.!?！？\n;；:：]\s*|(?:幫我|幫忙|替我|請|給我|\bplease|\bcan you|\bcould you|\bwould you|\bhelp me|"
           r"\bi want you to|\bi[’']?d like you to)\s*)((hey|hi|ok|okay|now|so|then|just|also|pls|plz|let[’']?s|and)\b[,\s]*){0,3}"
           r"(draw|paint|sketch|illustrate)\b" + EN_IDIOM)
IMAGE = re.compile(
    DRAW + r"|" + GEN_ZH_ACT + GAP + r"{0,12}" + IMAGE_NOUN + NOT_TEXT +
    r"|" + IMG_MEASURE +
    r"|" + DRAW_EN +
    r"|\b(i'?d like|i want|i need)\b" + SEP + r"{0,20}\b(image|picture|photo|illustration|drawing|painting|wallpaper|poster|logo)s?\b"
    r"|" + GEN_EN + SEP +
    r"{0,30}\b(image|picture|photo|illustration|wallpaper|poster|logo|portrait|artwork|art|drawing|painting|icon)s?\b", _I)
IMAGE_EDIT = re.compile(r"(改成|變成|轉成|換成|風格化|重繪|修成|修圖|上色|p成|加上|去背|畫風|風格|restyle|make it|"
                        r"\b(turn|convert|transform|change|make)\s+(it|this|that|the|my)\b" + SEP + r"{0,20}?\binto\b)", _I)
# 附圖 +「turn / convert this photo into an animation / GIF」是做影片
TO_VIDEO_EN = re.compile(r"\b(turn|convert|transform|change|make)\b" + SEP + r"{0,30}?\binto\b" + SEP +
                         r"{0,30}?\b(animations?|animated|gifs?|videos?|clips?|movies?|cinemagraphs?)\b", _I)
# 各種統計圖也不是要生成藝術圖
IMAGE_BLOCK = re.compile(r"(流程圖|架構圖|心智圖|甘特圖|圖表|統計圖|長條圖|圓餅圖|折線圖|直方圖|散佈圖|柱狀圖|餅圖|趨勢圖|曲線圖|"
                         r"表格|uml|er ?圖|類別圖|地圖路線|chart|diagram|graph)", _I)
# 看圖提問 vs 客氣的修圖請求（「這張圖可以改成夜景嗎」是要修圖）
QUESTION = re.compile(r"(是什麼|什麼|哪|嗎|？|\?|描述|分析|解釋|說明|看看|辨識|識別|翻譯|文字)")
REAL_QUESTION = re.compile(r"(是什麼|什麼|哪|描述|分析|解釋|說明|看看|辨識|識別|翻譯|文字)")
POLITE_EDIT = re.compile(r"(可以|可不可以|能不能|能否|能|幫我|麻煩)" + SEP + r"{0,12}(改成|變成|轉成|換成|修成|上色|去背|重繪|p成)"
                         r"|\b(can|could) you\b" + SEP + r"{0,20}\b(make it|restyle|(turn|convert|transform|change|make)\s+(it|this|that|the|my)\b" +
                         SEP + r"{0,20}?\binto\b)", _I)
# 沒附圖時，只有明確指「剛剛那張圖 / 上一張」才算修改上一張（「把剛剛的回答改成英文」不算）
IMG_REF = re.compile(r"(上一張|上張|前一張|同一張|(剛剛|剛才|上面|前面)(的|那|這)?一?[張幅]?(圖|照片|圖片|相片|畫)|"
                     r"(剛剛|剛才|上面|前面)?(這|那)[張幅](圖|照片|圖片|相片|畫)|\b(previous|last|that|this|same) (image|picture|photo)\b)", _I)

# 「寫一篇關於音樂的文章」是寫作，不是作曲
WRITING = re.compile(r"(文章|作文|報告|故事|小說|詩|信件|一封信|email|郵件|摘要|文案|講稿|劇本|評論|論文|介紹文|心得|"
                     r"(影片|短片|廣告|拍攝|youtube ?)腳本|分鏡|"
                     r"\b(essay|article|story|poem|letter|review|summary)\b)", _I)
# 寫作名詞後面緊接著作品名詞時只是修飾語（故事書封面、小說封面、詩意的鋼琴曲）；「寫一封信給喜歡聽音樂的朋友」不算
MOD_MEDIA = re.compile(r"(書|集|本)?[^，。,.!?！？\n給寄送跟和與向對]{0,3}?(" + IMAGE_NOUN + "|" + MUSIC_NOUN + "|" + VIDEO_NOUN + ")", _I)
# 附上圖片問「這是什麼」「幫我看看」是看圖，不是畫圖
VISION = re.compile(r"(看看|看一下|幫我看|分析|描述|辨識|識別|說明|解釋|翻譯|讀出|是什麼|是誰|哪裡|多少|有幾|\bwhat\b|\bdescribe\b|\bidentify\b)", _I)
STRONG_GEN = re.compile(r"(生成|產生|創作|設計|製作|做一張|做成|來一張|改成|變成|轉成|換成|重繪|畫|繪)", _I)

# 多個意圖衝突時，用「中心語」決定（中文中心語在後：音樂播放器→播放器；英文到介系詞為止）
HEAD = {
    "code": re.compile(r"(程式|程序|代碼|代码|原始碼|函式|函數|腳本|網頁|網站|頁面|app|應用|遊戲|小工具|工具|計算機|外掛|機器人|播放器|編輯器|"
                       r"下載器|api|爬蟲|\bbot\b|\bgames?\b|\bwebsite\b|\bweb ?page\b|\btool\b|\bprogram\b|\bscript\b|\bfunction\b|"
                       r"\bplayer\b|\bcode\b|指令|命令|命令列|終端機|批次檔|ffmpeg|imagemagick|yt-dlp|\bbash\b|powershell|\bshell\b)|" + TECH, _I),
    "image": re.compile(IMAGE_NOUN + r"|\b(image|picture|photo|illustration|wallpaper|poster|logo|portrait|artwork|art|drawing|painting)s?\b", _I),
    "music": re.compile(MUSIC_NOUN + r"|\b(music|song|beat|track|melody|soundtrack|tune|jingle|bgm)s?\b", _I),
    "video": re.compile(VIDEO_NOUN + r"|動起來", _I),
    "model3d": re.compile(r"(模型|建模|公仔|手辦|\bglb\b|\bmesh\b|3d|low[ -]?poly)", _I),
}
CLAUSE = re.compile(r"[，。,;；！!？?\n：:]")
PP_EN = re.compile(r"\s(for|of|with|about|in|on|that|which|to|using|from)\s", _I)
# 句首客套詞；「寫一個腳本把圖片縮小」「做一個網站讓使用者上傳影片」的「把 / 讓」後面是受詞，不是中心語
LEAD_ZH = re.compile(r"^\s*((請|麻煩|你|幫我|幫忙|替我)\s*)+")
OBJ_ZH = re.compile(r"(把|將|讓|幫我)")
ANIMATE = re.compile(r"動起來|動態化|讓(它|這張|圖片?)" + SEP + r"{0,4}動")   # 「生成一張海報讓它動起來」仍是影片
# 技術詞一定是程式；「遊戲 / 網站」只有是中心語時才算（「create a low-poly 3D tree for my game」是 3D）
CODE_OVER_3D = re.compile(r"(three\.?js|html|程式|unity|unreal|godot|webgl)", _I)
CODE_OVER_3D_NOUN = re.compile(r"(網頁|網站|遊戲|app|game|website)", _I)
PURPOSE_EN = re.compile(r"\bfor (my|our|your|a|an|the|his|her|their|this)\b" + SEP + "*", _I)
CODE_OVER_MEDIA = re.compile(r"(程式|代碼|代码|腳本|script|code|網頁|網站|app|函式|function)", _I)


def _head(c, found):
    best, pos = None, -1
    for f in found:
        rx = HEAD.get(f)
        for m in (rx.finditer(c) if rx else ()):
            if m.end() > pos:
                best, pos = f, m.end()
    return best


def head_intent(t, found):
    """第一個有中心語的子句裡，最後出現的作品名詞決定意圖（「把 / 讓」之前的部分優先）。"""
    for c in CLAUSE.split(t):
        if not c.strip():
            continue
        if c.isascii():
            m = PP_EN.search(c)
            if m:
                c = c[:m.start()]
        else:
            c = LEAD_ZH.sub("", c)
            m = OBJ_ZH.search(c)
            if m and c[:m.start()].strip() and not ANIMATE.search(c, m.start()):
                h = _head(c[:m.start()], found)
                if h:
                    return h
        h = _head(c, found)
        if h:
            return h
    return None


CMD_RE = re.compile(r"^\s*[/／](\S+)\s*")
ROUTE_HEAD, ROUTE_TAIL = 3000, 1000


def _clip(t):
    """超長訊息（貼上整篇文章 / 程式碼）只看開頭與結尾：指令通常在這兩處，也避免每條規則都掃過上萬字。"""
    return t if len(t) <= ROUTE_HEAD + ROUTE_TAIL else t[:ROUTE_HEAD] + "\n" + t[-ROUTE_TAIL:]


def parse_command(text):
    """「/畫 一隻貓」「／音樂輕快的爵士」「/3d椅子」「/聊 你好」→ (intent, 其餘文字)。
    只有指令沒有內容（例如「/畫」）時回傳空字串，讓伺服器回頭問要做什麼。"""
    m = CMD_RE.match(text or "")
    if not m:
        return None, text
    tok = m.group(1).lower()
    if tok in COMMANDS:
        return COMMANDS[tok], text[m.end():].strip()
    # 中文指令可以直接接內容；英文指令後面要接非英數字（/3d椅子 可以，/imagine、/3dprint 不算）
    for cmd in CMD_ORDER:
        if tok.startswith(cmd) and (not cmd.isascii() or not tok[len(cmd)].isascii()):
            return COMMANDS[cmd], re.sub(r"^[\s:：,，]+", "", text[m.start(1) + len(cmd):]).strip()
    return None, text


def candidates(text, has_image=False, has_prev=None):
    """回傳所有符合規則的意圖（依優先順序），供判斷與測試使用。
    has_prev：這個對話裡是否已經有上一張生成的圖；None 代表呼叫端沒提供，只看文字是否明確指「剛剛那張圖」。"""
    t = _clip(text.strip())
    found = []
    howto = bool(HOWTO.search(t)) or (bool(ABILITY.search(t)) and not REQUEST.search(t))
    info_hard = bool(INFO_HARD.search(t))
    info = info_hard or (bool(INFO_SOFT.search(t)) and not STRONG_REQ.search(t))
    code = bool(CODE_WORDS.search(t) or CODE_BUILD.search(t) or USE_TECH.search(t) or (CODE_WEAK.search(t) and not info_hard))
    gen_verb = bool(GEN_VERB.search(t))

    if SEARCH.search(t) and not howto and not (SELF_REF.search(t) and not SIMILAR.search(t)):
        found.append("search")
    if MODEL3D.search(t) and not howto and not (info and not gen_verb) and not IMAGE_BLOCK.search(t):
        found.append("model3d")
    if MUSIC.search(t) and not howto and not (info and not MUSIC_GEN.search(t)):
        found.append("music")
    # 附圖，或（有上一張圖時）明確指「剛剛那張圖 / 上一張」，才把「改成…」當成修圖
    edit_ctx = has_image or ((has_prev is None or has_prev) and IMG_REF.search(t))
    if (VIDEO.search(t) or (has_image and re.search(r"(做成|變成|轉成)" + SEP + r"{0,4}(影片|動畫|視頻)|動起來", t))
            or (edit_ctx and TO_VIDEO_EN.search(t))) and not howto and not (info and not gen_verb):
        found.append("video")
    img = IMAGE.search(t) and not IMAGE_BLOCK.search(t)
    if edit_ctx and IMAGE_EDIT.search(t) and (not QUESTION.search(t) or (POLITE_EDIT.search(t) and not REAL_QUESTION.search(t))):
        img = True
    if img and has_image and VISION.search(t) and not STRONG_GEN.search(t):
        img = False
    if img and not howto and not (info and not DRAW_RE.search(t)):
        found.append("image")
    if code:
        found.append("code")

    # 寫作名詞是受詞時才丟掉生成意圖；只是修飾語（故事書封面、詩意的鋼琴曲）時保留
    ws = list(WRITING.finditer(t))
    if ws and not COMPOSE.search(t) and not MOD_MEDIA.match(t, ws[-1].end()):
        drop = {"music", "video"} | (set() if DRAW_RE.search(t) else {"image"})
        found = [f for f in found if f not in drop]

    # 搜尋素材時提到的「圖片 / 影片」不是要生成
    if "search" in found:
        found = [f for f in found if f not in ("image", "video")]
    # 「做一個 3D 網頁遊戲」「three.js」「用 Unity 寫 3D 遊戲」是寫程式
    if "code" in found and "model3d" in found and (CODE_OVER_3D.search(PURPOSE_EN.sub("", t)) or
                                                   (CODE_OVER_3D_NOUN.search(t) and head_intent(t, found) != "model3d")):
        found.remove("model3d")
    # 「用 Python / CSS / canvas …」一定是程式；其他 code 與生成意圖衝突時看中心語（音樂播放器→程式，網站 logo→圖）
    if "code" in found and USE_TECH.search(t):
        found = [f for f in found if f in ("code", "search")]
    elif "code" in found and len(found) > 1:
        h = head_intent(t, found)
        if h:
            found = [h]
        elif CODE_OVER_MEDIA.search(t):
            found = [f for f in found if f in ("code", "search")]
    elif len(found) > 1:
        h = head_intent(t, found)
        if h:
            found = [h] + [f for f in found if f != h]
    return found


def detect(text, has_image=False, has_prev=None):
    """回傳 (intent, prompt, confident)。confident=False 代表規則有衝突，建議交給 LLM 裁決。
    斜線指令只有指令沒有內容時 prompt 是空字串。"""
    forced, rest = parse_command(text)
    if forced:
        return forced, rest, True
    found = candidates(text, has_image, has_prev)
    if not found:
        return "chat", text, True
    if len(found) == 1:
        return found[0], text, True
    # 圖 + 影片：candidates 已依中心語排序（YouTube 影片的封面→圖；沒有中心語時預設影片）
    if set(found) == {"video", "image"}:
        return found[0], text, True
    return found[0], text, False


def classify_prompt(text):
    return ("Classify the user's request into exactly one label: chat, code, image, video, music, model3d, search.\n"
            "- image: wants a NEW picture generated/drawn, or an attached picture edited/restyled\n"
            "- video: wants a video or animation generated\n"
            "- music: wants music/song/beat AUDIO generated\n"
            "- model3d: wants a 3D model/object generated\n"
            "- search: wants to FIND existing images/videos/material on the internet\n"
            "- code: wants code, a program, a website or a game written or debugged\n"
            "- chat: anything else (questions, conversation, explanations, writing text, news, advice)\n"
            "Answer with the label only.\n\n"
            f"Request: {text}\nLabel:")


def parse_label(out, fallback="chat"):
    m = re.search(r"\b(chat|code|image|video|music|model3d|search)\b", out or "", _I)
    return m.group(1).lower() if m else fallback


# 明確指上一個作品（「剛剛睡醒的貓」的「剛剛」只是副詞，不算）
REF_STRONG = re.compile(r"(上一張|上張|前一張|同一張|這張|那張|這幅|那幅|這個畫面|上面那|(剛剛|剛才)(的|那|這|生成|畫|做|產生|給)|"
                        r"previous|last (image|picture|one)|this (image|picture)|that (image|picture)|same (image|picture))", _I)
# 「它 / it」要看句子裡有沒有新的主體：「畫一隻貓，讓它戴帽子」的它是新畫的貓
REF_WEAK = re.compile(r"((?<!其)它|\bit\b)", _I)
NEW_SUBJECT = re.compile(r"(畫|生成|產生|做|來|給我|設計|繪製?)" + SEP + r"{0,3}(一|兩|三|幾|\d+)\s*[隻張幅個位名座輛棵朵條顆台]"
                         r"|^\s*(please\s+)?(draw|paint|sketch|generate|create|make|render|design)\s+(me\s+)?(an?|some|\d+)\b", _I)


def wants_previous(text):
    t = _clip(text)
    return bool(REF_STRONG.search(t) or (REF_WEAK.search(t) and not NEW_SUBJECT.search(t)))


# 英文用 \b 整字比對；中文虛詞只在安全位置刪除（不再把「酒吧」「查理」「去年」「去背」「目的地」切壞）
SEARCH_STRIP = re.compile(
    r"(請|幫我|幫忙|給我|可以用|能用|可以|麻煩|我想要|我想|我要|想要|需要|有沒有|上網|網路上|網上|去(?=找|搜|查|網|下載)|"
    r"找一下|找找看|找找|找些|尋找|搜尋一下|搜一下|搜索|搜尋|搜|(?<!尋)找|查一下|^\s*查|(?<=[我忙])查|蒐集|收集|下載|"
    r"一些|一張|幾張|幾個|幾部|多張|相關|有關|關於|(?<!目)的(?![士確])|圖片|照片|素材|圖庫|參考圖|參考|影片|視頻|高清|高畫質|免費|"
    r"(嗎|吧|呢)\s*$|"
    r"\b(please|find|search for|search|look for|get me|show me|me|a few|a couple of|a couple|several|some|images?|pictures?|photos?|"
    r"videos?|footage|stock|references?|of|about|download|free)\b)", _I)


def search_query(text):
    q = re.sub(r"[，。,.!?！？「」『』\"'：:；;、（）()]", " ", text)
    q = SEARCH_STRIP.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q or text.strip()


def search_kind(text):
    if SEARCH_STILL.search(text):   # 「影片的截圖」要的是圖
        return "images"
    return "videos" if SEARCH_VIDEO.search(text) else "images"


# 不刪 song/music/beat（MusicGen 需要）、不刪「make it …」（修圖指令）、容許「Hey,」與數量
EN_PREFIX = re.compile(
    r"^\s*((hey|hi|ok|okay)\b[,\s]*)?(please\s+)?((can|could|would) you\s+)?(please\s+)?"
    r"(draw|paint|sketch|generate|create|make|render|compose|produce|design|build|give me)(?!\s+(it|this|that)\b)"
    r"\s+(me\s+)?(an?\s+|some\s+|the\s+|\d+\s+)?((images?|pictures?|photos?|illustrations?|videos?|animations?|clips?|3d models?|models?)"
    r"\s+(of|about|with|for|that)\s+)?", _I)


def clean_prompt(text):
    """英文指令去掉「draw me a picture of」之類的前綴，中文交給 LLM 翻譯時處理。"""
    return EN_PREFIX.sub("", text).strip() or text
