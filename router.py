"""意圖路由：所有功能都在同一個聊天室，由這裡判斷使用者想做什麼。

先用規則快速判斷（不耗 CPU），只有在規則互相衝突時才請本機 LLM 做最後裁決。
"""
import bisect
import re

INTENTS = ("chat", "code", "image", "video", "music", "model3d", "search", "project")

LABELS = {
    "chat": "對話", "code": "程式", "image": "圖像", "video": "影片",
    "music": "音樂", "model3d": "3D 模型", "search": "素材搜尋", "project": "專案",
}

COMMANDS = {
    "畫": "image", "圖": "image", "圖片": "image", "圖像": "image", "image": "image", "img": "image", "draw": "image",
    "影片": "video", "動畫": "video", "視頻": "video", "video": "video",
    "音樂": "music", "歌": "music", "作曲": "music", "music": "music", "song": "music",
    "3d": "model3d", "3d模型": "model3d", "模型": "model3d", "建模": "model3d", "model": "model3d",
    "搜": "search", "搜尋": "search", "搜圖": "search", "找": "search", "search": "search", "find": "search",
    "程式": "code", "code": "code",
    "聊": "chat", "聊天": "chat", "對話": "chat", "chat": "chat",
    "專案": "project", "project": "project", "build": "project", "打包": "project",
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

# ---------------- 專案：要「新生成的素材（3D / 圖 / 音樂）＋ 遊戲 / 網頁」，而且分好幾步、要匯出打包或另外附上素材
# 單純寫程式（貪食蛇網頁遊戲、three.js 3D 網頁）或單一素材（3D 太空船模型）不算，要佔住工作佇列 10–30 分鐘
# 「遊戲角色」「遊戲截圖」「網站 logo」「game art」裡的遊戲 / 網站只是修飾語，不是要做的東西
PJ_OUT = re.compile(
    r"(遊戲(?!\s*(角色|人物|主角|截圖|畫面|背景|音樂|配樂|音效|素材|美術|道具|場景|風格|封面|海報|圖|icon|logo|模型|原畫|立繪|開場|主題曲|bgm|引擎|"
    r"公司|產業|業界|主機|手把|評論|推薦|心得|攻略|直播|實況|影片|設計師|用|劇情|故事|企劃|設定|文案|介紹|名稱|標題))|"
    r"(網頁|網站)(?!\s*(背景|圖|logo|icon|素材|配色|設計稿|截圖|橫幅|banner|封面|美術|文案|文章|內容|標題|介紹|名稱|網址|seo|流量|排名))|"
    r"頁面|html|webgl|three\.?js|"
    r"(互動|沉浸式)\S{0,2}(體驗|故事|展示|作品)|\bgames?\b(?!\s+(characters?|art|artwork|music|soundtrack|assets?|sprites?|screenshots?|"
    r"engines?|consoles?|studios?|trailers?|reviews?|icons?|logos?|covers?|posters?))|\b(website|web ?pages?|web ?apps?|browser|interactive)\b)", _I)
# 「我的遊戲」「放到網站上」「介紹網頁遊戲的文章」「on my website」是現有的地方或話題，不是要做的產出
PJ_OUT_NOT = re.compile(r"((我的|我們的|你的|現有|原本的?|這款|那款|這個|那個|這些|介紹|關於|有關|\bmy|\bour|\bexisting|\babout)\s*\S{0,2}|"
                        r"(放到|放在|放上|放進|上傳到?|傳到|貼到|貼在|分享到|發到|發佈到|發布到|嵌入到?|加到|加進|掛到|用在|用於)\S{0,4}|"
                        r"\b(on|onto|to) (my|our|the|your)|\babout\s+(\w+\s+)?)\s*$", _I)
PJ_CODE = re.compile(r"(程式|代碼|原始碼|前端|遊戲邏輯|javascript|\bjs\b|\bcode\b|\bcoding\b)", _I)   # 步驟裡的「再寫程式」
PJ_ASSET = re.compile(
    r"((3d|三維|立體)\s*(模型|建模|角色|人物|物件|道具|素材|資產|公仔)|建模|模型|\bglb\b|\bgltf\b|low[ -]?poly|低多邊形|"
    r"插圖|插畫|貼圖|立繪|原畫|概念圖|角色圖|背景圖|素材圖|精靈圖|圖片|圖像|美術(?![館系課班])|海報|封面圖|素材|"
    r"背景音樂|音樂|配樂|bgm|主題曲|歌曲|樂曲|曲子|旋律|鋼琴曲|進行曲|搖籃曲|小夜曲|奏鳴曲|協奏曲|狂想曲|交響曲|舞曲|純音樂|伴奏|音軌|\bost\b|"
    r"(?<![詩唱])歌(?![詞手星單唱劇迷名廳頌])|"
    r"\b3d(\s+[\w-]+){0,2}?\s+(models?|meshes|assets?|characters?|objects?|props?)\b|\bmodels? of\b|"
    r"\b(illustrations?|artwork|concept art|sprites?|textures?|images?|pictures?|art|music|soundtrack|theme song)\b)", _I)
# 「把圖片…」「我的插圖」「使用者上傳圖片」「不要音樂」「without music」是現有的或不要的，不用生成
PJ_NOT_NEW = re.compile(r"(把|將|這|那|此|我的|我們的|你的|現有|已有|原本|原有|上傳|載入|匯入|導入|讀取|不用|不要|不需要|不必|無需|免|沒有|別|"
                        r"去掉|拿掉|移除|播放|顯示|展示|瀏覽|預覽|分享|管理|編輯|處理|壓縮|轉換|辨識|搜尋|下載|列出|收藏)\S{0,3}\s*$|"
                        r"\b(my|our|your|their|existing|uploaded?|upload|load|import|no|without|remove|users?'?s?|play(s|ing)?|display(s|ing)?|"
                        r"show(s|ing)?|view(ing)?|browse|share|manage|edit|download)\s+(\w+\s+)?$", _I)
# 「音樂播放器」「3D 模型檢視器」「圖片上傳」「音樂遊戲」「插畫風格」是功能或修飾語
PJ_FEATURE = re.compile(r"\s*(播放器|檢視器|瀏覽器|編輯器|上傳|下載|功能|轉檔|轉換|壓縮|辨識|識別|搜尋|管理|分類|相簿|輪播|庫|網站|網頁|遊戲|作品集|風格|"
                        r"產生器|生成器|app|players?|viewers?|editors?|uploads?|galler(y|ies)|converters?|loaders?|generators?|search|"
                        r"sites?|websites?|games?|library|slider|carousel|style)", _I)
PJ_NOT_3D = re.compile(r"(訓練|微調|部署|推論|語言|資料|數據|機器學習|深度學習|神經網路|ai|預測|分類|回歸|統計|數學|商業|財務|物理|大型|llm|gpt|ml|"
                       r"data|language|machine learning|trained|train)\S{0,2}\s*$", _I)   # 「分類模型」「train a model」是 AI 模型
# 清單裡單獨的「3D 初號機、」也是素材（「3D 的」「3D 版」是修飾語）
PJ_3D_ITEM = re.compile(r"(3d|三維|立體)\s*(?![的版風感化]|效果|風格|視角|模式)([^\s，。,.!?！？、：:；;和跟與及]{1,6})"
                        r"(?=\s*([，。,.!?！？、：:；;和跟與及]|$))", _I)
PJ_3D_MOD = re.compile(r"(3d|三維|立體)\s*\S{0,2}?(場景|效果|畫面|視角|空間|世界|地圖|引擎|列印|印表|遊戲|網頁|網站|版|化|動畫|渲染|顯示|投影|環境|射擊|"
                       r"賽車|跑酷|迷宮|scenes?|effects?|views?|worlds?|engines?|print|games?|web|environments?|shooter|racer|platformer|graphics)", _I)
# 步驟標記：先…再… / 然後 / 最後 / then；「寫遊戲之前先…」也算分步
PJ_FIRST = re.compile(r"(首先|第一步|一開始|(?<![優事原預祖率])先(?!前|進|知|天|驅|鋒|烈|祖|輩|例|行|生(?![成產出])))", _I)
PJ_THEN = re.compile(r"(然後|接著|接下來|最後(?!一[關個次名頁局])|之後(?!再說)|之前|再來|隨後|完成後|做好後|弄好後|好了之後|其次|第[二三四五]步|"
                     r"\b(then|afterwards?|after that|finally|once (that|it)('s| is) done)\b)", _I)
PJ_AGAIN = re.compile(r"再(?![見次說也度會現版製]|利用|生(?![成產出]))")
PJ_EXPORT = re.compile(r"(匯出|導出|輸出|打包|封裝|存成|存為|包成|\bexport(ed|ing)?|\bpackag(e|ed|ing)|\bbundl(e|ed|ing))"
                       r"[^，。,.!?！？\n]{0,24}?(html|網頁|單一?檔|單檔|一個檔|檔案|離線|zip|\bfiles?\b|\bpages?\b)"
                       r"|打包|單一\s*(的\s*)?html|\b(single|standalone|self-contained|offline)[- ](html|file|page)\b|\bone html\b"
                       r"|(全部|一起|整個|整包|通通|都)\s*(匯出|導出|輸出)|(最後|然後|再)\s*(匯出|導出)(?=\s*[，。,.!！]|\s*$)"
                       r"|\b(export|package|bundle)\s+(it all|everything|all of it|the whole thing)\b|(可以|能|可)?離線(玩|使用|執行|開啟|遊玩|運行|打開|版)", _I)
# 另外附上素材（後面緊接著素材名詞才算）；「要有背景音樂」「with background music」只是遊戲功能，不算
PJ_ATTACH = re.compile(
    r"(附上|附帶|並附|附贈|連同|外加|另外(再)?(做|生成|產生|附|配|畫|建)|同時(生成|產生|製作|做|附|配)|並(且)?(幫我)?(生成|產生|製作|建立|建|做|附|配|譜|畫|設計)|"
    r"\b(with|including|plus|along with|together with|and)\s+((an?|its own|some|custom|original|generated|ai[- ]generated|\d+|two|three|matching)\s+)*"
    r"(low[- ]poly\s+)?(3d\s+(models?|characters?|assets?|meshes)|models?\s+of)\b|"
    r"\b(with|including|plus|along with|together with|and)\s+((an?|its own|some|matching)\s+)*(custom|original|generated|ai[- ]generated)\s+"
    r"(music|soundtrack|art|artwork|illustrations?|sprites?|textures?|images?|models?|assets?)\b|"
    r"\b(and|also|plus)\s+(then\s+)?(generate|create|make|compose|draw|model|design|render|paint)\b)", _I)
PJ_WORD = re.compile(r"(做|建立|建|開|產生|生成|製作|完成|弄|來|打造|新|完整|整個|全套)[^，。,.!?！？\n:：]{0,12}?"
                     r"(?<!這個)(?<!那個)(?<!我的)(?<!們的)(?<!你的)(?<![該本此])專案(?!管理|經理|經驗|計畫|進度|報告|會議)|"
                     r"\b(a|an|new|full|complete|whole|small|little|mini)\s+(\w+\s+){0,2}project\b"
                     r"(?!\s+(management|manager|plan|report|meeting|proposal))", _I)
# 不是專案：問方法 / 問意見、除錯、別的工具或語言、自己要做、已經做完的敘述
PJ_ASKING = re.compile(r"(怎麼|怎样|怎樣|如何|為什麼|为什么|為何|教我|教學|想學|要學|學會|自學|還是(先|直接|要先|用)|要不要|該不該|應該先|可行|是否|"
                       r"有沒有必要|值得|比較好|\bhow (do|to|can|should|would|much|long)\b|\bwhy\b|\bis it possible\b|\b(should|can|could|do) i\b|"
                       r"\b(should|do) we\b|\bwhat('s| is| are| should)\b|\bwhich (one|is|are|engine|tool|framework|library|should|would|do)\b)", _I)
PJ_ERR = re.compile(r"(打不開|開不了|跑不動|跑不起來|報錯|錯誤|\bbug|失敗|不見|壞掉|壞了|沒反應|卡住|閃退|白畫面|黑畫面|黑屏|白屏|不顯示|沒顯示|"
                    r"顯示不出|載入不了|讀不到|沒有?聲音|聽不到|看不到|沒有畫面|不能玩|動不了|不會動|出不來|跑版|太卡|很卡|\blag\b|\bno sound\b|"
                    r"can[’']?t (hear|see)|isn[’']?t (working|playing|loading|showing)|doesn[’']?t (work|play|load|show|run)|"
                    r"won[’']?t (load|work|open|play|show|run)|not working|\berror|\bcrash|\bbroken\b|\bfail(ed|s)?\b)", _I)
PJ_TOOL = re.compile(r"(\b(blender|maya|3ds ?max|c4d|cinema ?4d|zbrush|unity|unreal|ue[45]|godot|photoshop|illustrator|procreate|clip studio|"
                     r"krita|fl studio|garageband|ableton|cubase|logic pro|rpg ?maker|game ?maker|pygame|python|java|golang|rust|kotlin|flutter|"
                     r"excel|vba|matlab|sql|powershell|bash)\b|c\+\+|c#|c ?語言)", _I)
PJ_SELF = re.compile(r"(我|我們)\s*(自己|先去|要去|等一下|等等|待會|晚點|打算|準備|計畫|計劃|正在|在做)|自己(來|畫|做|建|寫|加|配|譜|處理)|"
                     r"\b(i|we)([’']ll| will| am going to|[’']m going to| plan to| am planning to|[’']m planning to)\b|\bmyself\b", _I)
PJ_SCHED = re.compile(r"(今天|明天|後天|今晚|明晚|晚點|待會|等一下|等等|下週|下禮拜|週末|早上|中午|下午|晚上)\s*(先|再|要|來|去|就)")
PJ_NARR = re.compile(r"(已經|剛剛|剛才|昨天|上次|前幾天|上週|上禮拜)[^，。,.!?！？\n]{0,10}?(做|寫|畫|建|生成|匯出|打包|輸出|弄|完成)(了|好|完|過)|"
                     r"(做|寫|畫|建|生成|匯出|打包|輸出)了[^，。,.!?！？\n]{0,12}?(結果|但|可是|卻)|"
                     r"\b(i|we)([’']ve| have)? (already |just )?(made|built|wrote|exported|created|generated|finished)\b", _I)
PJ_REQ = re.compile(r"(幫我|幫忙|替我|為我|給我|(?<!請)請(?!問)|麻煩|我要|想要|我需要|^\s*(你\s*)?(可以|可不可以|能不能|能否|能)|"
                    r"\b(can|could|would|will) you\b|\bplease\b|\bi (want|need)\b|\bi[’']?d like\b|\bi would like\b)", _I)
PJ_TAIL_Q = re.compile(r"(嗎|呢|？|\?)[\s。.!！~～]*$")
PJ_MAKE = re.compile(r"(做|寫|生成|產生|生出|建|製作|設計|打造|開發|弄|畫|繪|配|譜|作|來一|給我|幫我|幫忙|需要|想要|我要|"
                     r"\b(make|build|create|generate|write|code|develop|design|compose|draw|render|give me|produce|need|want|export|package|bundle)\b)", _I)
PJ_PLACEHOLDER = re.compile(r"(代替|取代|替代|佔位|占位|placeholders?|instead of)", _I)   # 「用方塊代替」是不要生成素材
PJ_FIND = re.compile(r"(找|搜|查|蒐集|收集|抓|\bfind|\bsearch|\blook for|\bget me|\bshow me|\bcollect)", _I)
PJ_WRITE = re.compile(r"(寫|撰寫)\s*(一|兩|幾)?\s*篇|\b(write|draft)\s+(an?\s+|the\s+|some\s+)?"
                      r"(article|essay|report|blog post|post|review|summary)s?\b", _I)   # 「寫一篇介紹…的文章」是寫作
# 「（你可以）先幫我生成主角模型」：先 + 生成動詞也是新的一步
PJ_FIRST_GEN = re.compile(r"(首先|第一步|(?<![優事原預祖率首])先)\s*(幫我|幫忙|替我|請你?|你)?\s*(生成|產生|生出|做|建模|建|畫|繪|設計|製作|配|譜|打造)")


def _pj_cuts(t):
    """步驟分界的位置：然後 / 最後 / then …，「先…再…」或句首的「再」，以及「先 + 生成動詞」。"""
    cuts = [m.start() for m in PJ_THEN.finditer(t)] + [m.start() for m in PJ_FIRST_GEN.finditer(t) if m.start() > 0]
    f = PJ_FIRST.search(t)
    for m in PJ_AGAIN.finditer(t):
        i = m.start()
        if (f and f.start() < i) or i == 0 or t[i - 1] in "，,、；;:： \t\n":
            cuts.append(i)
    return sorted(cuts)


PJ_KIND_3D = re.compile(r"(3d|三維|立體|建模|模型|glb|gltf|poly|多邊形|model|mesh)", _I)
PJ_KIND_MUSIC = re.compile(r"(音樂|配樂|bgm|曲|歌|旋律|奏|音軌|\bost\b|music|soundtrack|song)", _I)


def _pj_assets(t, cuts):
    """要新生成的素材：[(位置, 種類 3d / image / music / None)]。素材名詞要排除現有的 / 不要的 / 功能 / AI 模型；
    某個步驟本身就是生成請求（先畫一隻貓、先做一台 3D 戰車）也算。"""
    out = []
    for m in PJ_ASSET.finditer(t):
        s, w = m.start(), m.group(0)
        pre = t[max(0, s - 8):s]
        if PJ_NOT_NEW.search(pre) or PJ_FEATURE.match(t, m.end()):
            continue
        if re.match(r"(模型|建模|model)", w, _I) and PJ_NOT_3D.search(pre):
            continue
        out.append((s, "3d" if PJ_KIND_3D.search(w) else "music" if PJ_KIND_MUSIC.search(w) else None if w == "素材" else "image"))
    for m in PJ_3D_ITEM.finditer(t):
        if not (PJ_OUT.search(m.group(0)) or PJ_3D_MOD.search(m.group(0)) or PJ_NOT_NEW.search(t[max(0, m.start() - 8):m.start()])):
            out.append((m.start(), "3d"))
    bounds = [0] + cuts + [len(t)]
    for a, b in zip(bounds, bounds[1:]):
        step = t[a:b]
        if not step.strip() or PJ_OUT.search(step) or CODE_WORDS.search(step) or CODE_BUILD.search(step):
            continue
        if MODEL3D.search(PJ_3D_MOD.sub(" ", step)):
            out.append((a, "3d"))
        elif IMAGE.search(step) and not IMAGE_BLOCK.search(step):
            out.append((a, "image"))
        elif MUSIC.search(step):
            out.append((a, "music"))
    return out


def _project(t):
    """多步驟的遊戲 / 網頁專案：要新生成的素材 + 遊戲 / 網頁產出，而且 (1) 要匯出打包 / 明說是專案 / 要兩種以上素材，
    (2) 另外附上素材，或 (3) 素材和程式分在不同步驟（先…再…）。問方法、除錯、自己要做、別的工具都不算。"""
    if not PJ_OUT.search(t) or not PJ_MAKE.search(t) or PJ_ASKING.search(t) or PJ_ERR.search(t) or PJ_TOOL.search(t) or \
            PJ_SELF.search(t) or PJ_NARR.search(t) or PJ_PLACEHOLDER.search(t) or PJ_WRITE.search(t):
        return False
    if INFO_HARD.search(t) or (ABILITY.search(t) and not REQUEST.search(t)) or \
            ((PJ_TAIL_Q.search(t) or PJ_SCHED.search(t)) and not PJ_REQ.search(t)):
        return False
    m = SEARCH.search(t)
    if m and PJ_FIND.match(m.group(0)):
        return False
    outs = [m.start() for m in PJ_OUT.finditer(t) if not PJ_OUT_NOT.search(t[max(0, m.start() - 8):m.start()])]
    if not outs:
        return False
    cuts = _pj_cuts(t)
    found = _pj_assets(t, cuts)
    if not found:
        return False
    assets = [p for p, k in found]
    # 要打包成 HTML / 明說是專案 / 同時要兩種以上的素材（3D + 音樂…）
    if PJ_EXPORT.search(t) or PJ_WORD.search(t) or len({k for p, k in found if k}) >= 2:
        return True
    if any(m.start() <= p <= m.end() + 16 for m in PJ_ATTACH.finditer(t) for p in assets):
        return True
    # 先…再…：素材和遊戲 / 程式分在不同步驟
    if not cuts:
        return False
    outs += [m.start() for m in PJ_CODE.finditer(t)]
    step = lambda p: bisect.bisect_right(cuts, p)
    sa, so = {step(p) for p in assets}, {step(p) for p in outs}
    return not (len(sa) == 1 and sa == so)


def wants_project(text):
    """多步驟的遊戲 / 網頁專案（要生成素材再寫程式、打包成 HTML）。"""
    return _project(_clip((text or "").strip()))


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
    if _project(t):
        return ["project"]
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
