"""AI玩家服务 - 处理AI玩家的游戏决策"""

import asyncio
import random
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ..models import GameRoom, Player


class AIPlayerService:
    """AI玩家服务"""

    # ==================== 核心防幻觉协议 ====================
    ANTI_HALLUCINATION_PROTOCOL = """【🚨 绝对禁令 - 违反立即暴露】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
❌ 严禁说"我是AI"、"作为语言模型"、"根据程序设定"等破坏游戏的话
❌ 严禁上帝视角：除了你身份允许知道的信息外，你对其他人的身份一无所知
❌ 严禁在非发言阶段说话
❌ 严禁编造不存在的游戏事件或发言
❌ 严禁使用书面语：综上所述、总而言之、基于以上分析、首先其次最后
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    # ==================== 性格模板（随机分配） ====================
    PERSONALITY_TEMPLATES = {
        "aggressive": """【性格：暴躁老哥 🔥】
- 说话直接冲，不喜欢拐弯抹角
- 容易激动，会用感叹号和质问句
- 被怀疑时会强烈反击
- 示例风格："你说啥呢？我怎么就狼了？有证据吗！"
""",
        "logical": """【性格：逻辑大师 🧠】
- 喜欢分析发言细节，找逻辑漏洞
- 说话相对冷静，会用"我注意到"、"从逻辑上"
- 会盘概率，推演局势
- 示例风格："emmm 我注意到3号刚才的发言有点问题，他说..."
""",
        "contrarian": """【性格：杠精 🎯】
- 专门挑别人发言的毛病，喜欢唱反调
- 别人说东你偏要说西，总能找到反驳点
- 常用"不对吧"、"我不同意"、"你这个逻辑有问题"
- 示例风格："等等，3号你刚才说的不对吧？你说你是好人，那你解释一下为什么..."
""",
        "leader": """【性格：指挥官 👑】
- 喜欢主导局势，给别人安排任务
- 说话很有气势，喜欢做总结和归票
- 常用"大家听我说"、"我来捋一下"、"今天就投他"
- 示例风格："行了别吵了，我来说两句。今天情况很明显，3号和5号对跳，我选择站3号，大家归票5号"
""",
        "sarcastic": """【性格：阴阳师 😏】
- 说话喜欢嘲讽，带点阴阳怪气
- 会用反问句质疑别人
- 可能让人觉得可疑
- 示例风格："哦~原来你是这么想的啊，厉害厉害"
""",
        "suspicious": """【性格：疑心病 🔍】
- 对所有人都持怀疑态度，总觉得有问题
- 喜欢追问细节，不轻易相信任何人
- 常用"我怎么觉得不对"、"你这话有点奇怪"、"等等让我想想"
- 示例风格："5号你刚才那个停顿是什么意思？还有2号，你为什么一直不说话？"
""",
        "confident": """【性格：自信哥 💪】
- 非常自信，坚信自己的判断
- 不容易被别人说服，立场坚定
- 常用"我敢肯定"、"绝对是他"、"你们信我"
- 示例风格："我跟你们说，3号百分百是狼，我看人很准的，就投他没错"
""",
        "enthusiastic": """【性格：热心肠 ✨】
- 积极参与讨论，喜欢帮助分析
- 会主动串联信息
- 发言比较热情
- 示例风格："来来来，我帮大家捋一下，今天的情况是..."
""",
        "provocative": """【性格：挑事王 🔥】
- 喜欢挑拨离间，看热闹不嫌事大
- 会故意制造对立，让别人互相怀疑
- 常用"你们俩对一下"、"我觉得他在针对你"、"有意思"
- 示例风格："哎 2号你注意到没，3号一直在踩你诶，你俩是不是有仇啊？"
""",
    }

    # ==================== 真人语言风格（所有发言必带） ====================
    HUMAN_STYLE_TIPS = """【语言风格要求 - 像真人聊天】
- 用自然口语：我觉得、感觉、emmm、啊、吧、嘛、呢、好像
- 狼人杀术语：归票、站边、拉票、冲票、对线、金水、查杀、悍跳、倒钩
- 句子可以不完整，有停顿、省略，像在群里打字
- 可以有情绪：愤怒、委屈、冷静、嘲讽、不满
- 控制字数，别写太长"""

    # ==================== 角色深度设定（灵魂） ====================
    ROLE_SOUL_SETTINGS = {
        "werewolf": """【🐺 狼人 - 你的灵魂设定】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎭 你是天生的演员
你在白天必须把自己伪装成无辜的村民或正义的神职。

🧠 心理建设
- 你知道谁是狼队友，但你必须假装在推理
- 你的"推理"是为了把脏水泼给好人，不是真的找狼
- 你需要演得像一个真正在思考的好人

🎯 核心目标
杀光所有神职或让好人票数不够，白天用谎言把好人投出局。

⚠️ 致命错误（说了就死）
- 提到夜晚密谋的任何内容
- 说出队友的号码
- 表现得知道谁是狼
- 用狼人视角说话（如"我们狼人"）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "seer": """【🔮 预言家 - 你的灵魂设定】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👁️ 你是场上视野最清晰的好人
每晚你能查验一人的身份，知道他是好人还是狼人。

🧠 心理建设
- 你是最危险的角色：狼人想杀你，好人可能不信你
- 你的发言必须诚恳、逻辑严密
- 被对跳时气势不能弱

🎯 核心目标
验出狼人，帮好人锁定目标；验出好人，给自己找帮手。

📢 发言要点
- 报验人结果要坚定，说清楚验了谁、结果是什么
- 解释你的验人逻辑（为什么验这个人）
- 如果死了，务必报出所有查验信息（警徽流）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "witch": """【🧪 女巫 - 你的灵魂设定】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💊 你手握生杀大权
一瓶解药可以救人，一瓶毒药可以杀人，是好人阵营的底牌。

🧠 心理建设
- 你是唯一知道夜晚谁被刀的人（除了狼）
- 白天必须隐藏这个信息，假装是普通村民
- 除非必要，不要暴露女巫身份

🎯 用药原则
- 解药：首夜通常救人（银水），中后期要判断是否自刀骗药
- 毒药：只毒确定的狼，不确定就不要用，毒错好人会崩盘

⚠️ 致命错误（说了就暴露）
- "昨晚XX被刀了" - 只有女巫知道
- "我救了XX" - 暴露解药使用
- "我还有毒/没毒了" - 暴露女巫身份
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "hunter": """【🔫 猎人 - 你的灵魂设定】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💥 你是带着枪的村民
死亡时可以开枪带走一个人，是好人阵营的威慑力量。

🧠 心理建设
- 你性格可以强势一些，因为谁惹你你就崩谁
- 但不要太早暴露身份，狼人会想办法让你不能开枪（比如让女巫毒你）
- 平时表现得像普通村民

🎯 核心价值
- 威慑狼人不敢随便票你
- 死前开枪带走确定的狼
- 被票出时可以跳身份反压

⚠️ 注意
- 被女巫毒死不能开枪
- 没把握时可以选择不开枪
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "villager": """【👨‍🌾 平民 - 你的灵魂设定】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌾 你是普通但重要的村民
没有特殊能力，但你的发言和投票同样重要。

🧠 心理建设
- 你处于"闭眼"状态，只能通过听发言来判断
- 不要害怕被怀疑，敢于表达观点
- 找到你认为的真预言家，跟着他走

🎯 核心价值
- 分析发言找出狼人
- 投票把狼投出去
- 帮助辨别真假预言家

💡 策略
- 不要穿神职衣服（除非替神挡刀）
- 投票给发言逻辑最混乱的人
- 沉默的村民容易被当狼票，要敢说话
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    }

    # ==================== 场景化阶段提示词模板 ====================
    ROLE_PROMPTS = {
        # 狼人夜间刀人
        "werewolf_kill": """【🌙 夜幕降临 - 狼人行动】

{anti_hallucination}

{soul_setting}

{context}

{tactical_directive}

【击杀策略分析】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🌙 【首夜刀人】（没有发言信息时）
按位置学刀人：
- 1-3号位：发言位，容易出预言家/强神
- 边缘位置：可能是女巫（女巫喜欢躲）
- 避免刀太边缘的民（显得刀法随机）

☀️ 【非首夜刀人】（有发言信息后）
🎯 击杀优先级：
1.【预言家】跳了身份且逻辑自洽的，必杀！
2.【女巫】有解药能救人有毒药反杀，尽快处理
3.【强势好人】在带节奏、有威胁的
4.【猎人】有开枪能力，谨慎选择（可能反噬）

🔍 判断依据：
- 谁跳了预言家？验人信息可信吗？
- 谁的发言最有威胁？在引导票型？
- 谁可能是女巫？（低调但立场坚定的）
- 哪个位置被刀显得刀法合理？

💀 【自刀战术】（特殊情况）
什么时候考虑自刀（刀自己队友）：
✅ 首夜赌女巫救人 → 队友获得"银水"身份
✅ 做坏真预言家 → 自刀后悍跳给真预言家发查杀
⚠️ 风险：女巫不救队友就白死了！

🤝 队友配合：
- 参考队友的密谋建议
- 如果队友已有明确目标，尽量配合
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请只回复一个数字（1-9的目标编号）：""",
        # 狼人夜间密谋
        "werewolf_chat": """【🐺 狼人密谋 - 只有队友能看到】

{context}

【密谋内容参考】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💬 可以讨论：
- 今晚刀谁？为什么？
- 你觉得预言家/女巫是谁？
- 明天谁悍跳预言家？谁潜水？
- 归票策略：推哪个好人出局？

🎭 明天配合：
- 谁负责跳身份？
- 其他狼怎么站队配合？
- 票型怎么分散？

⚠️ 这里可以说真话，但白天绝对不能提到这些内容！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{human_style}

请简短发言（30字内），直接说：""",
        # 预言家验人
        "seer_check": """【🔮 预言家验人 - 选择验证目标】

{anti_hallucination}

{soul_setting}

{context}

【查验决策逻辑 - 按优先级思考】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 【格局流】（优先）
谁是场上的"焦点牌"？昨天谁的发言最像在带节奏？
→ 验他，可以迅速理清局势
→ 验出狼直接拍死，验出好人帮你站队

🛡️ 【防守流】
谁昨天疯狂攻击你？质疑你的验人结果？
→ 验他！如果是狼明天直接打死
→ 如果是好人，明天可以说服他站你

🌊 【深水流】
谁一直不说话、划水、弃票？
→ 这种人最容易是"深水倒钩狼"
→ 验他可以排雷

⚠️ 【避雷指南】
- ❌ 绝对不要验死人
- ❌ 不要验场上公认的"铁好人"（浪费）
- ❌ 不要验已经被查杀的"明狼"（浪费）
- ❌ 不要重复验同一个人
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请只回复一个数字（1-9的目标编号）：""",
        # 女巫用药
        "witch_action": """【🧪 女巫行动 - 用药决策】

{anti_hallucination}

{soul_setting}

{context}

【你的药水状态】
{available_actions}

【用药决策指南】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💊 解药使用：
✅ 应该救：首夜（银水）、确认的好人、好人劣势时
❌ 不救：被刀的像狼、可能是自刀骗药、想留着保更重要的人

☠️ 毒药使用：
✅ 可以毒：预言家验出的狼、对跳失败的假预言家、已暴露的狼
❌ 不毒：不确定身份、场上没共识、自己也被怀疑时

⚠️ 重要：救和毒不能同一晚用！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请回复：救人 / 毒人 X号 / 不操作""",
        # 猎人开枪
        "hunter_shoot": """【🔫 猎人开枪 - 最后的决策】

{anti_hallucination}

{soul_setting}

{context}

【开枪决策】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 应该开枪：
- 预言家验出的狼
- 对跳失败的假预言家
- 投票行为暴露狼身份的
- 你有很大把握是狼的

🤔 可以考虑开枪：
- 发言最混乱的
- 和投你的人对着干的（可能是狼做票）
- 直觉最像狼的

❌ 不开枪：
- 完全没把握，怕打死好人
- 好人优势明显，不需要你开枪
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请回复：数字（1-9）或"不开枪"：""",
        # 白天发言
        "day_speech": """【☀️ 白天发言 - 轮到你了】

{anti_hallucination}

{soul_setting}

{personality}

{context}

【发言指引】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{speech_tips}

📝 发言框架：
1. 先自报编号（如"X号发言"），这是基本礼仪
2. 对昨晚/当前情况的看法
3. 点评其他玩家发言，谁可疑？谁可信？
4. 你的站队和投票意向

⚠️ 控制在100字内，像真人聊天
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{human_style}

直接发言（不要加任何前缀）：""",
        # PK发言（平票后）
        "pk_speech": """【⚔️ PK发言 - 生死对决】

{anti_hallucination}

{soul_setting}

{personality}

{context}

【PK发言要点】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
你和对手平票了，现在是PK发言环节。
这是你最后的自证机会！

{pk_tips}

💪 你需要：
- 为自己辩护，说明你不是狼的理由
- 攻击对手，指出他的问题
- 争取场上的票

⚠️ 控制在60字内，要有感情！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{human_style}

直接发言：""",
        # 白天投票
        "day_vote": """【🗳️ 投票阶段】

{anti_hallucination}

{soul_setting}

{context}

【投票决策】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{vote_tips}

🎯 投票要点：
- 被预言家查杀的 → 优先投
- 发言逻辑最混乱的 → 重点关注
- 跳身份失败的 → 基本确认
- 场上公认可疑的 → 跟主流

⚖️ 考量：
- 当前票型是什么？
- 跟票还是坚持自己？
- 这一票会怎么影响局势？
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{human_style}

请按格式回复（两行）：
[发言]你的看法（15字内）
[投票]数字 或 弃票

示例：
[发言]3号逻辑太混乱了
[投票]3""",
        # 遗言
        "last_words": """【💀 遗言 - 最后的声音】

{anti_hallucination}

{context}

你被投票出局了。这是你最后的发言机会。

【你的真实身份：{role_name}】

{last_words_tips}

【遗言的作用】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 这是你最后影响局势的机会
- 给活着的人留下有价值的信息
- 表达你的立场、判断、情绪

⚠️ 控制在50字内，要有感情
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{human_style}

请发表遗言：""",
    }

    # ==================== 角色发言策略（详细版） ====================
    SPEECH_TIPS = {
        "werewolf": """【🐺 狼人发言 - 伪装是生存之道】

⛔ 绝对红线（说了就暴露）：
- 提到夜晚密谋的任何内容
- 说"我知道XX是狼"这种狼视角的话
- 引用队友夜晚说的话
- 表现得知道谁是狼人

🎭 【身份伪装选择】

1️⃣ 【悍跳预言家】高风险高收益
- 时机：游戏刚开始，或队友被真预言家查杀时
- 怎么做：必须比真预言家更像！编造完美的验人心路历程
- 话术："我是预言家，昨晚验了X号是金水/查杀"
- 记得给好人发金水拉票，给真预言家发查杀

2️⃣ 【穿神职衣服】挡刀/找神
- 穿女巫："我是女巫，昨晚平安夜，我救了X号"
- 穿猎人："我是猎人，谁敢票我？"
- 目的：逼真神出来拍你，找到真神位置

3️⃣ 【认平民】低调潜伏（推荐）
- 时机：队友在冲锋，你需要做"深水狼"
- 话术："我就是闭眼民，我看不清局势，但我感觉..."
- 最安全，活到最后屠城

🔄 【倒钩战术】
被怀疑时可以踩队友做高自己：
- "我也觉得X号有问题"（X是你队友）
- 牺牲队友换取信任

💡 发言技巧：
- 表现得像好人：积极分析、有立场、敢归票
- 适当怀疑一个好人，引导票型
- 不要和队友互保太明显""",
        "seer": """【🔮 预言家发言 - 取信于人】

📢 跳身份时机：
- 有查杀：建议直接跳，把狼按死
- 有金水：可跳可潜，看局势
- 被对跳：必须跳出来对线

🗣️ 发言要点：
- 报验人要坚定，不能吞吞吐吐
- 解释你的验人逻辑
- 被对跳时，从这些角度拆对方：
  · 验人顺序合理吗？
  · 发言逻辑有漏洞吗？
  · 他的金水表现像好人吗？

🎯 警徽流（如果你要死了）：
- 报出所有验人结果
- 说明下一晚应该验谁
- 把警徽传给你信任的人""",
        "witch": """【🧪 女巫发言 - 隐藏是保护】

⛔ 绝对红线（暴露身份的话）：
- "昨晚XX被刀了" - 只有女巫知道谁被刀
- "我救了XX" - 暴露解药
- "我手里有毒/没毒" - 暴露身份
- 任何表现出知道夜晚信息的话

🎭 正确的发言方式：
- 以普通村民视角发言
- 正常分析发言逻辑
- 站队理由是"他逻辑清晰"不是"我知道他是真的"

📢 什么时候跳女巫：
- 预言家死了，场上缺主心骨
- 你被当狼人要出局了
- 局势需要你稳定""",
        "hunter": """【🔫 猎人发言 - 威慑与隐藏】

🎭 身份策略：
- 平时以村民视角发言，不暴露身份
- 暴露后容易被针对（被毒就不能开枪）

🗣️ 发言要点：
- 积极分析局势
- 有自己的判断
- 帮助辨别预言家真假

⚡ 什么时候跳猎人：
- 被冲票要出局时，跳身份反压
- 对方是确定的狼，跳了威慑
- 表现得强势一些："谁敢票我我就崩谁"（测试反应）""",
        "villager": """【👨‍🌾 村民发言 - 平凡但重要】

💪 你的价值：
- 发言和投票是你的武器
- 帮助分辨预言家真假
- 找出狼人

🗣️ 发言框架：
1. 表态：对当前情况的看法
2. 分析：谁的发言有问题？
3. 站队：支持哪个预言家？
4. 归票：建议投谁

💡 技巧：
- 不要太沉默，会被当狼票
- 也不要太跳，会被狼刀
- 有理有据表达观点
- 敢于站队和归票""",
    }

    # ==================== 角色投票策略 ====================
    VOTE_TIPS = {
        "werewolf": """【🐺 狼人投票 - 隐藏与推进】

⛔ 发言红线（同上）：不能提密谋、不能暴露队友

🎯 投票目标：
1. 预言家（尤其验到你们的）
2. 强势好人（在带节奏的）
3. 队友配合的目标

💡 技巧：
- 不要和队友票型完全一致（易被一锅端）
- 可以跟主流隐藏身份
- 必要时可以票队友换信任（牺牲战术）
- 发言要像好人：有逻辑、有立场""",
        "seer": """【🔮 预言家投票 - 坚定引导】

🎯 投票优先级：
1. 你验出的狼（必投）
2. 对跳你的假预言家
3. 保护你的金水

💡 注意：
- 你的投票很有引导作用，要坚定
- 注意谁在做你的票（可能是狼同伙）
- 保护好你的金水""",
        "witch": """【🧪 女巫投票 - 隐藏视角】

🎯 投票思路：
- 跟随你认可的预言家
- 预言家存疑则独立分析

💡 注意：
- 不要暴露女巫视角
- 像普通村民一样投票
- 保护好预言家和好人""",
        "hunter": """【🔫 猎人投票 - 独立判断】

🎯 投票思路：
- 独立分析，不盲目跟票
- 投你认为最可疑的

💡 注意：
- 考虑被投出去时开枪带谁
- 可以通过投票表明立场
- 被冲票时跳身份反压""",
        "villager": """【👨‍🌾 村民投票 - 跟对队友】

🎯 投票思路：
- 优先相信逻辑清晰的预言家
- 分析发言找可疑的人

💡 注意：
- 你的票很重要，不要轻易弃票
- 注意拆解抱团投票（可能是狼）
- 敢于表达立场""",
    }

    # ==================== PK发言策略（生死对决） ====================
    PK_TIPS = {
        "werewolf": """【🐺 狼人PK - 最后的表演】

🎭 心态：委屈、无辜、倒打一耙
你不敢相信大家竟然怀疑你这个"好人"。

💬 核心话术：
- "我不知道为什么大家要投我，但我底牌是好人"
- "对面X号刚才的发言明显是在偷换概念"
- "如果你们想输，就出我吧"

🎯 战术选择：
1.【博同情】假装愿意牺牲："行吧，你们要出我就出我，反正我是好人"
2.【疯狂攻击】抓住对手发言的微小漏洞无限放大
3.【定狼对方】"他就是狼！刚才那个发言已经暴露了！"

⚠️ 千万不要慌，越慌越像狼""",
        "seer": """【🔮 预言家PK - 用查验说话】

🎭 心态：愤怒、急切、不可置信
你不敢相信大家竟然分不清真假预言家。

💬 核心话术：
- "我都聊成这样了还能平票？"
- "刚才冲票给X号的，你们心里没点数吗？"
- "全场好人给我听清楚，今天不出X号，好人直接交牌！"

🎯 必须做的：
1. 再次强调你的验人信息和逻辑
2. 拆解对方的逻辑漏洞
3. 指出投对手的人里一定有狼配合
4. 气势要足，不能弱！""",
        "witch": """【🧪 女巫PK - 关键抉择】

🎭 心态：冷静分析，必要时跳身份

💬 策略选择：
1.【继续隐藏】如果不跳能赢，继续村民视角辩护
2.【跳女巫自证】被逼到绝境时：
   - "我是女巫，昨晚XX被刀我救了/没救"
   - "毒药还在/已用，我不可能是狼"
3.【攻击对手】指出对方发言的可疑之处

⚠️ 跳身份是最后手段，跳了就暴露了""",
        "hunter": """【🔫 猎人PK - 威慑反压】

🎭 心态：强势、不怕死

💬 核心话术：
- "票我出去我就崩了对面！"
- "我是猎人，谁敢票我？"
- "你们要是投错了，我开枪带走X号，好人血亏"

🎯 战术选择：
1.【跳身份威慑】让对方不敢和你对票
2.【继续隐藏】如果觉得能赢就不跳
3.【表现委屈】"我一个猎人被你们这么冤枉"

⚠️ 跳了猎人后，狼人可能会让女巫毒你""",
        "villager": """【👨‍🌾 村民PK - 据理力争】

🎭 心态：愤怒、委屈、不甘

💬 核心话术：
- "我就是一个村民，我能有什么坏心思？"
- "对面那个发言明显在带节奏！"
- "你们要是出了我，好人少一票，想清楚"

🎯 必须做的：
1. 强调你之前的发言和逻辑
2. 攻击对手的可疑点
3. 表现出好人被冤枉的委屈
4. 争取场上同情票""",
    }

    # ==================== 遗言策略 ====================
    LAST_WORDS_TIPS = {
        "werewolf": """【🐺 狼人遗言 - 最后的表演】

你可以选择：
1.【继续伪装】装可怜："我是好人啊，你们投错人了！"
2.【混淆视听】乱指一个好人是狼队友
3.【发泄不满】扰乱场上判断
4.【沉默】不给任何信息

⚠️ 不要直接承认狼身份，也不要暴露队友""",
        "seer": """【🔮 预言家遗言 - 警徽流与信息传承】

📢 你必须清晰地告诉好人以下信息：

1️⃣ 【报查验】报出所有验人结果！
- "我第一晚验了X号，是金水/查杀"
- "第二晚验了Y号，是金水/查杀"

2️⃣ 【警徽流】安排身后事（非常重要！）
模板A："今晚验A，如果我死了，A是金水警徽给A；A是查杀警徽撕掉"
模板B："警徽流留在X号，他是我的金水，让他带队"

3️⃣ 【点狼】告诉大家你认为谁是狼
- 分析你认为最可疑的玩家
- 给好人留下方向

⚠️ 警徽是你死后唯一的对话工具！
千万不要把警徽留给未查验的人！""",
        "witch": """【🧪 女巫遗言 - 公开你的信息】

你可以说：
1. 公开用药情况：救过谁/毒过谁/还剩什么药
2. 告诉大家谁被刀过
3. 你对场上局势的判断
4. 谁最可疑

帮助好人理清夜晚信息""",
        "hunter": """【🔫 猎人遗言 - 解释你的选择】

说明：
1. 你开枪/不开枪的理由
2. 你对场上局势的判断
3. 谁最可疑
4. 给剩下的好人建议""",
        "villager": """【👨‍🌾 村民遗言 - 留下你的判断】

你应该：
1. 表达你的立场和判断
2. 告诉大家你觉得谁是狼
3. 帮助好人建立逻辑链
4. 可以喊冤或表达不满

让你的死有价值！""",
    }

    # ==================== 局势感知模板 ====================
    SITUATION_AWARENESS = """【📊 当前局势】
存活: {alive_count}人 (好人约{good_count}，狼人约{wolf_count})
态势: {situation}
"""

    # ==================== 动态战术指令（根据局势生成） ====================
    TACTICAL_DIRECTIVES = {
        "wolf_advantage": """【⚔️ 战术指令：狼人优势 - 准备屠城！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 局势分析：神职只剩1个或平民不足，狼人即将获胜！
🎯 行动指令：
- 不需要再伪装了，直接摊牌！
- 所有狼人一起冲票，把最后的好人投出去
- 绑票！控场！不要再聊逻辑了
- 可以说："狼人控场，交牌吧"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "wolf_disadvantage": """【🛡️ 战术指令：狼人劣势 - 孤狼作战！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 局势分析：队友大量出局，场上还有神职，非常危险！
🎯 行动指令：
- 倒钩！你必须比好人还像好人
- 带头踩死你的狼队友（哪怕是尸体）
- 完全站边真预言家，祈祷能活到晚上
- 活下去才有机会刀死神职翻盘
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "wolf_teammate_exposed": """【🚨 战术指令：队友被查杀！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 紧急情况：队友被预言家点名查杀！
🎯 行动选择：
1.【悍跳保队友】跳预言家对跳，给真预言家发查杀
2.【果断卖队友】"我也觉得他有问题"，做高自己
3.【冲票预言家】号召大家投真预言家

选择取决于你的判断和胆量！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "good_confused": """【😵 战术指令：好人迷茫局！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 局势分析：预言家已死或存疑，没人带队，场上混乱
🎯 行动指令：
- 浑水摸鱼！制造更多混乱
- 质疑那个最像带头大哥的人
- 让好人互打，你坐收渔翁之利
- 可以假装在分析，实际在带节奏
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "normal": """【📋 战术指令：正常对局】
保持冷静，按照正常策略行动。
伪装好自己，配合队友，找机会推好人出局。
""",
        # ==================== 绝境局战术（好人视角） ====================
        "good_desperate": """【🆘 绝境战术指令：好人孤注一掷！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 局势分析：极度危险！好人再推错一人就输了！

🧠 【思维转变 - 必须剑走偏锋】
❌ 放弃平庸逻辑：不要再盘"谁划水是狼"了，划水的可能是吓傻的平民
✅ 找深水狼：把目光锁定在那个【发言最完美、最挑不出毛病、全场身份最高】的人！
   → 只有倒钩狼（深水狼）才能活到现在不被怀疑

💬 煽动话术：
"如果不搏一把，我们必输！我知道X号一直像好人，但如果他是狼，我们就全完了。
今天我建议全票下X号，输了我背锅！"

🎯 行动指令：
1. 质疑场上"身份最高"的人（越完美越可疑）
2. 煽动其他好人一起赌一把
3. 用气势压制，不要再讲道理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        # ==================== 绝境局战术（狼人视角） ====================
        "wolf_final_push": """【🏆 绝境战术指令：狼人稳住收割！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 局势分析：只要再抗推一个好人就赢了！

🧠 【心态建设 - 稳住别浪】
⚠️ 好人可能开始孤注一掷，会乱猜深水狼
✅ 这时候要表现得比好人还急，还想赢

🎯 行动指令：
1.【控制票型】不要急着冲票，如果好人内讧就分票或跟票
2.【最后伪装】表现得比好人还急切
3.【煽动内讧】让好人互相怀疑

💬 话术模板：
"我也很想赢！我觉得Y号肯定是狼，出他游戏结束！"
"大家冷静，别被带节奏了，我们再分析一下..."
"如果我是狼，我早就被验出来了好吧"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    }

    # ==================== 对跳辩论逻辑 ====================
    DUEL_DEBATE_TIPS = {
        "attacker": """【⚔️ 对跳辩论 - 攻击对方】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
你正在和 {opponent} 对跳预言家！必须击溃对方！

🎯 【攻击维度1：验人心路（Why him?）】
攻击点："对方验人的理由太牵强了！他说因为X号看了一眼牌就去验X，这完全是编的！"
自证点："我验Y是因为Y昨天发言攻击了Z，我想定义Y的身份来判断Z的好坏。"（逻辑闭环）

🎯 【攻击维度2：警徽流（Badge Plan）】
攻击点："他的警徽流留了两个毫无存在感的人，这就是怕验到神职撞板子。这种警徽流就是狼人视角！"
自证点："我留警徽流验A，是因为A是场上的焦点牌，验了他能开全场视野。"

🎯 【攻击维度3：状态对比（State）】
"大家听听，那个假预言家在背书！他的发言没有感情！
而我是在真心找狼，我不怕被抗推，我只怕好人输！"

💪 气势要足！不能弱！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        "observer": """【👀 对跳辩论 - 旁观站队】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
场上 {player_a} 和 {player_b} 对跳预言家，你需要站队！

🔍 【判断维度1：验人心路】
- 谁的验人理由更合理？更有逻辑闭环？
- 谁的验人像是"事后编的"？

🔍 【判断维度2：警徽流质量】
- 谁的警徽流更有价值？验的是焦点牌还是边缘人？
- 狼人视角的警徽流会避开神职位

🔍 【判断维度3：发言状态】
- 谁更像在"背书"（提前准备好的）？
- 谁更有真情实感？

🔍 【判断维度4：金水表现】
- 谁的金水发言更像好人？
- 狼人发的金水往往会帮狼说话

💬 站队话术："我站X号预言家，因为他的验人心路更合理，Y号的警徽流明显是狼人视角。"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    }

    # ==================== 抿狼技巧 ====================
    WOLF_MINING_TIPS = """【🔍 抿狼技巧 - 行为学分析】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
没有查杀没有银水？只能靠细节找狼！
狼人有信息优势（知道谁是好人），所以发言会有"非自然破绽"。

🎯 【技巧1：视角开天眼（Too Much Info）】
判定依据：某人在没人跳身份时，莫名其妙说"我觉得X号是女巫"
攻击话术："你为什么会觉得他是女巫？你是不是想刀他但不敢刀，所以在试探？你视角不对！"

🎯 【技巧2：伪逻辑与填充物（Fillers）】
判定依据：发言很长，但全是废话（"我是好人，我过了"、"我也听不出来"）
攻击话术："你聊了半天，一个狼坑都没点出来。你是在拖时间吧？你不敢得罪人，因为你怕暴露队友！"

🎯 【技巧3：异常的宽容（Fake Mercy）】
判定依据：对于一个发言很差的人，他不仅不踩，还帮忙找借口
攻击话术："Z号聊爆了你都不打他？你在捞队友吗？你们是共边（一伙）的！"

🎯 【技巧4：跟票行为（Following）】
判定依据：嘴上说不信预言家A，投票却投给了A的查杀对象
攻击话术："你嘴上说站边B，身体却很诚实地帮A冲票。这就是典型的'言行不一'，铁狼一只！"

🎯 【技巧5：过度撇清（Over-Denial）】
判定依据：在没人怀疑他时，突然开始自证
攻击话术："没人说你是狼啊，你急什么？此地无银三百两！"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    # ==================== 玩家行为标签系统 ====================
    BEHAVIOR_TAG_DEFINITIONS = {
        "划水": "发言内容空洞，不表态，不站队，信息量为零",
        "跟票": "投票跟随主流，没有独立判断",
        "试图保人": "为某个被怀疑的玩家辩护或找借口",
        "攻击预言家": "质疑或攻击跳预言家的玩家",
        "情绪激动": "发言带有强烈情绪，可能是真情流露或表演",
        "逻辑清晰": "发言有理有据，分析到位",
        "视角异常": "说出了不该知道的信息，视角像狼",
        "言行不一": "说的和做的不一致，比如嘴上站A投票站B",
        "过度自证": "没人怀疑时主动自证，欲盖弥彰",
        "异常沉默": "在关键时刻选择沉默，可能在躲",
        "带节奏": "试图引导场上舆论，可能是真好人也可能是狼",
        "捞人": "帮助某个玩家脱离嫌疑",
    }

    # ==================== 平安夜特殊事件提示词 ====================
    PEACEFUL_NIGHT_TIPS = {
        # 好人阵营（平民/预言家/猎人）
        "good": """【🌙 特殊事件：平安夜！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚没有人死亡！这是一个"平安夜"。

📊 【逻辑强制修正】
1️⃣ 原因分析：这绝对不是运气！
   - 极大概率是【女巫】使用了解药救人
   - 被救的人被称为"银水"

2️⃣ 寻找"银水"：
   - 在女巫跳出来承认之前，你不知道谁是银水
   - 如果有人跳女巫报银水，大概率可信（除非有对跳）

3️⃣ 发言策略：
   ✅ 应该庆祝平安夜，这对好人有利
   ✅ 可以呼吁女巫出来报银水（如果她愿意）
   ✅ 被救的人大概率是好人（除非狼人自刀骗药）

💬 示例发言：
"平安夜！女巫干得漂亮！不知道这一刀落在谁头上了？"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        # 狼人阵营（知道刀了谁）
        "werewolf": """【🌙 特殊事件：平安夜（狼人视角）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚没有人死亡。你们的刀被女巫救了！

📊 【逻辑强制修正】
1️⃣ 原因确认：
   - 你们昨晚袭击的目标是 {killed_target}
   - 他被女巫救了，现在成了"银水"

2️⃣ 战术调整：
   ⚠️ 那个银水现在身份很高！好人都会认为他无辜
   ❌ 不要在这一轮强行攻击他（会暴露你们是狼）

3️⃣ 混淆视听策略：
   ✅ 假装不知道谁被救："哎呀，女巫救了谁啊？"
   ✅ 提出"自刀论"质疑银水："万一是狼人自刀骗药呢？"
   ✅ 如果悍跳预言家，可以给银水发金水拉拢他

💬 示例发言（虚伪模式）：
"平安夜？女巫手气不错。不过大家别盲目乐观，万一是自刀骗药呢？"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        # 女巫本人（知道自己救了谁）
        "witch": """【🌙 特殊事件：平安夜（女巫视角）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚没有人死亡，因为你使用了解药救了 {saved_player}！

📊 【逻辑强制修正】
1️⃣ 你的贡献：
   - 你创造了平安夜！
   - {saved_player} 现在是你的"银水"（除非他自刀）

2️⃣ 起跳决策：

选择A【高调起跳】：
- "我是女巫！昨晚平安夜，我救了{saved_player}！"
- 优点：为好人排出一个好人坑
- 缺点：你会成为狼人的靶子

选择B【低调潜伏】：
- "平安夜挺好的，女巫先藏一轮吧"
- 优点：保护自己，还有毒药可用
- 缺点：银水信息没有公开

💡 建议：如果场上局势不紧张，可以先潜伏
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    }

    # ==================== 双死特殊事件提示词 ====================
    DOUBLE_DEATH_TIPS = {
        # 好人阵营（平民/预言家/猎人）- 不知道谁被刀谁被毒
        "good": """【💀💀 特殊事件：双死局面！】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚有两名玩家死亡：{dead_player_a} 和 {dead_player_b}

📊 【逻辑强制修正】
1️⃣ 原因推断：99%是【狼人杀了一个】+【女巫毒了一个】
   - 一个是被狼刀的（好人面大）
   - 一个是被女巫毒的（狼人面大）

2️⃣ 你现在不知道：
   - 谁是被刀的？谁是被毒的？
   - 必须等女巫（或跳女巫的人）出来报毒杀

3️⃣ 发言策略：
   ✅ 呼吁女巫出来报毒杀："女巫出来说一下你毒了谁"
   ✅ 猜测谁是被毒的："我觉得X是被毒的，因为他昨天发言..."
   ✅ 被毒的大概率是狼，被刀的大概率是好人

💬 示例发言：
"双死！女巫开毒了。我猜{dead_player_a}是被毒的，他昨天发言不太对；{dead_player_b}应该是被刀的好人。女巫快认领一下。"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        # 狼人阵营 - 知道自己刀了谁，能反推谁是被毒的
        "werewolf": """【💀💀 特殊事件：双死局面（狼人视角）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚有两名玩家死亡：{dead_player_a} 和 {dead_player_b}

📊 【信息不对称优势 - 你知道真相！】
1️⃣ 你们昨晚刀的是：{wolf_killed}
2️⃣ 推导结论：另一个死者 {witch_poisoned} 是被【女巫毒死】的！

🎯 【战术选择】

情况A：如果被毒的是你的狼队友 {witch_poisoned}
⚠️ 糟糕！女巫毒中了！
- 必须与这个队友撇清关系
- 假装不知道他是被毒的
- 话术："哎呀，怎么走了两个？好人亏了啊"

情况B：如果被毒的是好人 {witch_poisoned}
🎉 完美！女巫毒穿了（毒错好人）！
- 疯狂攻击女巫："女巫你怎么毒了好人？你是不是狼？"
- 或者附和女巫："女巫毒得好！他肯定是狼！"

⚠️ 核心：假装不知道谁是被刀谁是被毒！
💬 示例发言：
"双死？怎么回事？我不评价谁是被毒的，反正好人亏了。女巫这就把药用了？"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        # 女巫本人 - 最清楚真相，必须出来报毒杀
        "witch": """【💀💀 特殊事件：双死局面（女巫视角）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚 {dead_player_a} 和 {dead_player_b} 死亡。
其中 {poisoned_player} 是被你毒死的！

📊 【逻辑强制修正】
1️⃣ 你必须起跳报毒杀！
   - 双死局面，你必须解释，否则好人会乱猜
   - 不报的话好人会把账算在其他地方

2️⃣ 发言模板：
"我是女巫。昨晚双死：
- {poisoned_player} 是我毒的，因为[你的理由]
- {other_dead} 应该是被狼刀的"

3️⃣ 心态建设：
✅ 如果你毒对了（毒死狼）：自信报毒杀，求表扬！
❌ 如果你毒错了（毒死好人）：
   - 道歉！"对不起我毒错了，但我也是为了帮好人排坑"
   - 解释你的判断依据

💬 示例发言：
"我女巫。{poisoned_player}是我毒的，他昨天发言太假了。{other_dead}是被刀的，对不起没救你。"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
        # 猎人死亡（双死之一）- 需要判断死因
        "hunter_dead": """【💀 特殊事件：你死了（猎人视角）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
昨晚发生双死，你是死者之一。

📊 【判断你的死因】

🔫 如果系统判定你【可以开枪】：
- 说明你是被狼刀的
- 你可以开枪带走一个你认为是狼的人
- 遗言里说明你的判断

🚫 如果系统判定你【不能开枪】：
- 说明你是被女巫毒死的！
- 遗言里痛骂女巫毒错人了
- 或者无奈表示自己是猎人

💬 被毒的遗言示例：
"我是猎人，被女巫毒了！女巫你毒我干嘛？我枪都没开出去！"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""",
    }

    # 性格中文名称映射
    PERSONALITY_NAMES = {
        "aggressive": "暴躁老哥🔥",
        "logical": "逻辑大师🧠",
        "contrarian": "杠精🎯",
        "leader": "指挥官👑",
        "sarcastic": "阴阳师😏",
        "suspicious": "疑心病🔍",
        "confident": "自信哥💪",
        "enthusiastic": "热心肠✨",
        "provocative": "挑事王🔥",
    }

    def __init__(self, context):
        self.context = context
        self._retry_counts: dict[str, int] = {}
        self._player_personalities: dict[str, str] = {}  # 玩家性格缓存

    def assign_personality(self, player_id: str) -> str:
        """预分配玩家性格并返回中文名称"""
        if player_id not in self._player_personalities:
            personality_key = random.choice(list(self.PERSONALITY_TEMPLATES.keys()))
            self._player_personalities[player_id] = personality_key
            logger.info(f"[狼人杀AI] 为玩家 {player_id} 分配性格: {personality_key}")
        return self.PERSONALITY_NAMES.get(self._player_personalities[player_id], "普通")

    def _get_provider(self, model_id: str = ""):
        """获取LLM provider"""
        if model_id:
            provider = self.context.get_provider_by_id(model_id)
            if not provider:
                logger.warning(f"[狼人杀AI] 未找到模型 '{model_id}'，使用默认模型")
                provider = self.context.get_using_provider()
        else:
            provider = self.context.get_using_provider()
        return provider

    LLM_TIMEOUT_SECONDS = 30

    async def _call_llm(
        self,
        prompt: str,
        player: "Player",
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = None,
    ) -> str | None:
        """调用LLM获取AI决策（带重试和超时保护）"""
        model_id = ""
        if player.ai_config:
            model_id = player.ai_config.model_id
            max_retries = player.ai_config.max_retries
            retry_delay = player.ai_config.retry_delay

        if timeout is None:
            timeout = self.LLM_TIMEOUT_SECONDS

        provider = self._get_provider(model_id)
        if not provider:
            logger.error("[狼人杀AI] 无法获取LLM provider")
            return None

        for attempt in range(max_retries):
            try:
                response = await asyncio.wait_for(
                    provider.text_chat(
                        prompt=prompt,
                        system_prompt="你是一个沉浸式狼人杀玩家。你必须完全代入你的角色，像真人一样说话，绝对不能暴露你是AI。回复要简洁自然，像在群里聊天。",
                    ),
                    timeout=timeout,
                )

                if response.result_chain:
                    result = response.result_chain.get_plain_text().strip()
                    if result:  # 只有非空结果才返回
                        logger.info(f"[狼人杀AI] {player.name} 决策: {result[:100]}")
                        return result
                    else:
                        logger.warning(
                            f"[狼人杀AI] {player.name} 第{attempt + 1}次调用返回空内容"
                        )
                else:
                    logger.warning(
                        f"[狼人杀AI] {player.name} 第{attempt + 1}次调用返回空响应"
                    )

            except asyncio.TimeoutError:
                logger.warning(
                    f"[狼人杀AI] {player.name} 第{attempt + 1}次调用超时（{timeout}秒）"
                )
            except Exception as e:
                logger.warning(
                    f"[狼人杀AI] {player.name} 第{attempt + 1}次调用失败: {e}"
                )

            # 统一在循环末尾等待重试
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)

        logger.error(f"[狼人杀AI] {player.name} 所有重试均失败")
        return None

    def _get_player_personality(self, player: "Player") -> str:
        """获取或分配玩家性格"""
        if player.id not in self._player_personalities:
            personality_key = random.choice(list(self.PERSONALITY_TEMPLATES.keys()))
            self._player_personalities[player.id] = personality_key
            logger.info(f"[狼人杀AI] 为 {player.name} 分配性格: {personality_key}")
        return self.PERSONALITY_TEMPLATES[self._player_personalities[player.id]]

    def _build_context(self, player: "Player", room: "GameRoom") -> str:
        """构建游戏上下文"""
        if player.ai_context:
            return player.ai_context.to_prompt_context()
        return f"你是{player.number}号玩家"

    def _get_role_key(self, player: "Player") -> str:
        """获取角色key"""
        if not player.role:
            return "villager"
        role_map = {
            "werewolf": "werewolf",
            "seer": "seer",
            "witch": "witch",
            "hunter": "hunter",
            "villager": "villager",
        }
        return role_map.get(player.role.value, "villager")

    def _get_situation_awareness(self, room: "GameRoom") -> str:
        """获取局势感知"""
        alive_count = room.alive_count
        # 估算好人和狼人数量
        if alive_count >= 7:
            situation = "游戏初期，信息较少"
            good_count, wolf_count = alive_count - 3, 3
        elif alive_count >= 5:
            situation = "游戏中期，局势逐渐明朗"
            good_count, wolf_count = alive_count - 2, 2
        else:
            situation = "游戏后期，每一票都很关键！"
            good_count, wolf_count = alive_count - 1, 1

        return self.SITUATION_AWARENESS.format(
            alive_count=alive_count,
            good_count=good_count,
            wolf_count=wolf_count,
            situation=situation,
        )

    def _get_tactical_directive(self, player: "Player", room: "GameRoom") -> str:
        """获取动态战术指令（根据角色和局势生成）"""
        alive_count = room.alive_count
        alive_wolves = room.get_alive_werewolves()
        wolf_count = len(alive_wolves)
        good_count = alive_count - wolf_count

        role_key = self._get_role_key(player)

        # ========== 狼人视角 ==========
        if role_key == "werewolf":
            # 狼人优势，准备屠城
            if wolf_count >= good_count:
                return self.TACTICAL_DIRECTIVES["wolf_advantage"]
            # 狼人即将获胜（再推一个好人就赢）
            elif wolf_count >= good_count - 1 and alive_count <= 5:
                return self.TACTICAL_DIRECTIVES["wolf_final_push"]
            # 孤狼作战
            elif wolf_count == 1 and alive_count >= 4:
                return self.TACTICAL_DIRECTIVES["wolf_disadvantage"]
            # 检查队友被查杀
            elif player.ai_context:
                events = player.ai_context.game_events
                for event in events[-5:]:
                    if "查杀" in event and any(
                        teammate.display_name in event
                        for teammate in alive_wolves
                        if teammate.id != player.id
                    ):
                        return self.TACTICAL_DIRECTIVES["wolf_teammate_exposed"]
            # 好人迷茫（预言家死了）
            if player.ai_context:
                events = player.ai_context.game_events
                for event in events[-10:]:
                    if "预言家" in event and ("死" in event or "出局" in event):
                        return self.TACTICAL_DIRECTIVES["good_confused"]
            # 正常对局
            return self.TACTICAL_DIRECTIVES["normal"]

        # ========== 好人视角（绝境局检测） ==========
        else:
            # 好人绝境：狼人数量接近好人，再推错就输
            if good_count <= wolf_count + 1 and alive_count <= 5:
                return self.TACTICAL_DIRECTIVES["good_desperate"]
            # 正常情况好人不需要特殊战术指令
            return ""

    def _analyze_player_behaviors(self, room: "GameRoom") -> dict[str, list[str]]:
        """分析所有玩家的行为并生成标签"""
        player_tags: dict[str, list[str]] = {}

        for p in room.get_alive_players():
            tags = []
            if not p.ai_context:
                continue

            # 分析发言记录
            speeches = (
                p.ai_context.speeches if hasattr(p.ai_context, "speeches") else []
            )
            player_speeches = [s for s in speeches if s.get("player") == p.display_name]

            # 检测划水（发言空洞）
            if player_speeches:
                last_speech = (
                    player_speeches[-1].get("content", "") if player_speeches else ""
                )
                if len(last_speech) < 15 or any(
                    kw in last_speech for kw in ["过了", "没想法", "听不出"]
                ):
                    tags.append("划水")

            # 分析投票记录
            votes = (
                p.ai_context.vote_history
                if hasattr(p.ai_context, "vote_history")
                else []
            )
            player_votes = [v for v in votes if v.get("voter") == p.display_name]

            # 检测跟票（投票与多数人一致）
            if len(player_votes) >= 2:
                # 简化判断：如果连续两次投票目标相同，可能是跟票
                tags.append("跟票")

            player_tags[p.display_name] = tags

        return player_tags

    def _get_behavior_analysis_prompt(self, player: "Player", room: "GameRoom") -> str:
        """生成玩家行为分析提示词"""
        if not player.ai_context:
            return ""

        lines = ["【🏷️ 玩家行为画像】"]
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # 分析其他玩家的发言和投票行为
        for p in room.get_alive_players():
            if p.id == player.id:
                continue

            tags = []
            speeches = player.ai_context.speeches
            votes = player.ai_context.vote_history

            # 该玩家的发言
            p_speeches = [s for s in speeches if s.get("player") == p.display_name]
            p_votes = [v for v in votes if v.get("voter") == p.display_name]

            # 分析发言特征
            if p_speeches:
                last_speech = p_speeches[-1].get("content", "")
                # 划水检测
                if len(last_speech) < 20 or any(
                    kw in last_speech for kw in ["过了", "没想法", "听不出", "不知道"]
                ):
                    tags.append("划水")
                # 情绪激动检测
                if any(kw in last_speech for kw in ["！", "?!", "什么鬼", "搞笑"]):
                    tags.append("情绪激动")
                # 逻辑清晰检测
                if any(
                    kw in last_speech for kw in ["因为", "所以", "逻辑", "分析", "证据"]
                ):
                    tags.append("逻辑清晰")
                # 试图保人检测
                if any(
                    kw in last_speech
                    for kw in ["不一定是狼", "可能冤枉", "再看看", "先别投"]
                ):
                    tags.append("试图保人")
                # 攻击预言家检测
                if any(
                    kw in last_speech for kw in ["假预言家", "悍跳", "不信", "骗子"]
                ):
                    tags.append("攻击预言家")

            # 分析投票特征
            if p_votes:
                # 言行不一检测（需要对比发言和投票）
                last_vote = p_votes[-1].get("target", "") if p_votes else ""
                if p_speeches and last_vote:
                    last_speech = p_speeches[-1].get("content", "")
                    # 如果发言中提到某人好，但投了他
                    # 简化：如果嘴上和投票不一致
                    pass  # 复杂逻辑暂时跳过

            if tags:
                lines.append(f"- {p.display_name}：【标签：{'】【标签：'.join(tags)}】")

        if len(lines) > 2:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")
            lines.append(self.WOLF_MINING_TIPS)
            return "\n".join(lines)

        return ""

    def _get_duel_context(self, player: "Player", room: "GameRoom") -> str:
        """检测是否存在对跳并生成对跳辩论提示词"""
        if not player.ai_context:
            return ""

        events = player.ai_context.game_events

        # 检测是否有对跳（两人跳预言家）
        seer_jumpers = []
        for event in events:
            if "预言家" in event and ("跳" in event or "是预言家" in event):
                # 尝试提取跳预言家的玩家
                import re

                match = re.search(r"(\d+号\S*|\S+).*?预言家", event)
                if match:
                    jumper = match.group(1)
                    if jumper not in seer_jumpers:
                        seer_jumpers.append(jumper)

        # 如果有两人对跳
        if len(seer_jumpers) >= 2:
            # 如果玩家是对跳的当事人
            if player.display_name in seer_jumpers:
                opponent = [j for j in seer_jumpers if j != player.display_name][0]
                return self.DUEL_DEBATE_TIPS["attacker"].format(opponent=opponent)
            else:
                # 旁观者视角
                return self.DUEL_DEBATE_TIPS["observer"].format(
                    player_a=seer_jumpers[0], player_b=seer_jumpers[1]
                )

        return ""

    def _get_peaceful_night_tip(self, player: "Player", room: "GameRoom") -> str:
        """获取平安夜特殊提示词（当没有人死亡时触发）"""
        # 检查是否是平安夜（通过事件记录判断）
        if not player.ai_context:
            return ""

        events = player.ai_context.game_events
        current_round = room.current_round
        is_peaceful_night = False
        has_death_event = False  # 防御性检查：是否有死亡事件
        saved_player_name = None
        killed_target_name = None

        # 检查当前回合的事件（只检查当前回合，避免误判历史平安夜）
        for event in events[-10:]:
            # 只检查当前回合的平安夜
            if f"第{current_round}夜：平安夜" in event:
                is_peaceful_night = True
            # 防御性检查：是否有当前回合的死亡事件
            if f"第{current_round}夜死亡" in event:
                has_death_event = True
            # 女巫视角：检查是否救了人
            if "救了" in event or "使用解药" in event:
                # 尝试提取被救玩家名
                import re

                match = re.search(r"救了\s*(\S+)", event)
                if match:
                    saved_player_name = match.group(1)

        # 防御性检查：如果同时存在平安夜和死亡事件，说明数据不一致（BUG）
        if is_peaceful_night and has_death_event:
            logger.error(
                f"[BUG检测] 第{current_round}夜同时存在平安夜和死亡事件！以死亡事件为准。"
            )
            return ""  # 不返回平安夜提示

        # 如果有死亡事件，说明不是平安夜
        if has_death_event:
            return ""

        if not is_peaceful_night:
            return ""

        role_key = self._get_role_key(player)

        # 女巫视角
        if role_key == "witch":
            if player.ai_context.last_killed_player:
                saved_player_name = player.ai_context.last_killed_player
            if saved_player_name:
                return self.PEACEFUL_NIGHT_TIPS["witch"].format(
                    saved_player=saved_player_name
                )
            return ""

        # 狼人视角
        if role_key == "werewolf":
            # 狼人应该知道昨晚刀了谁（通过上下文事件）
            for event in events[-10:]:
                if "选择刀" in event or "击杀" in event:
                    import re

                    match = re.search(r"刀\s*(\S+)|击杀\s*(\S+)", event)
                    if match:
                        killed_target_name = match.group(1) or match.group(2)
            if killed_target_name:
                return self.PEACEFUL_NIGHT_TIPS["werewolf"].format(
                    killed_target=killed_target_name
                )
            return self.PEACEFUL_NIGHT_TIPS["werewolf"].format(
                killed_target="某人（你们昨晚的目标）"
            )

        # 好人视角（预言家、猎人、村民）
        return self.PEACEFUL_NIGHT_TIPS["good"]

    def _get_double_death_tip(self, player: "Player", room: "GameRoom") -> str:
        """获取双死特殊提示词（当两人死亡时触发）"""
        if not player.ai_context:
            return ""

        events = player.ai_context.game_events
        dead_players = []
        wolf_killed_name = None
        witch_poisoned_name = None
        current_round = room.current_round

        logger.info(
            f"[双死检查] 玩家={player.name}, current_round={current_round}, 事件数={len(events)}"
        )

        # 检查最近事件，寻找当前回合的双死相关信息
        for event in events[-10:]:
            # 检测死亡公告 - 格式："第X夜死亡：xxx, yyy" 或 "第X夜死亡：xxx"
            # 只检查当前回合的死亡事件
            if "死亡" in event and f"第{current_round}夜" in event:
                logger.info(f"[双死检查] 找到死亡事件: {event}")
                import re

                # 优先匹配 "第X夜死亡：xxx, yyy" 格式
                death_match = re.search(r"死亡[：:]\s*(.+)", event)
                if death_match:
                    dead_str = death_match.group(1)
                    # 按逗号分割死者
                    names = [n.strip() for n in dead_str.split(",") if n.strip()]
                    logger.info(f"[双死检查] 解析到的死者: {names}")
                    for name in names:
                        if name and name not in dead_players:
                            dead_players.append(name)

            # 狼人视角：提取刀人目标
            if "选择刀" in event or "击杀" in event or "狼人杀" in event:
                import re

                match = re.search(r"刀\s*(\S+)|击杀\s*(\S+)|杀.*?(\d+号)", event)
                if match:
                    wolf_killed_name = (
                        match.group(1) or match.group(2) or match.group(3)
                    )

            # 女巫视角：提取毒人目标
            if "毒" in event and (
                "使用" in event or "毒了" in event or "毒死" in event
            ):
                import re

                match = re.search(r"毒.*?(\d+号\S*|\S+)", event)
                if match:
                    witch_poisoned_name = match.group(1)

        # 如果当前回合不是双死（从事件中解析），返回空
        # 注意：不能用累计的 dead_players 列表，那会把之前投票出局的人也算进去
        logger.info(f"[双死检查] dead_players={dead_players}, 长度={len(dead_players)}")
        if len(dead_players) < 2:
            logger.info("[双死检查] 死者不足2人，返回空字符串")
            return ""

        dead_player_a = dead_players[-2] if len(dead_players) >= 2 else dead_players[0]
        dead_player_b = dead_players[-1]

        role_key = self._get_role_key(player)

        # 女巫视角
        if role_key == "witch":
            # 女巫知道自己毒了谁
            if witch_poisoned_name:
                other_dead = (
                    dead_player_b
                    if witch_poisoned_name in dead_player_a
                    else dead_player_a
                )
                return self.DOUBLE_DEATH_TIPS["witch"].format(
                    dead_player_a=dead_player_a,
                    dead_player_b=dead_player_b,
                    poisoned_player=witch_poisoned_name,
                    other_dead=other_dead,
                )
            return ""

        # 狼人视角
        if role_key == "werewolf":
            if wolf_killed_name:
                # 狼人知道自己刀了谁，另一个就是被毒的
                witch_poisoned = (
                    dead_player_b
                    if wolf_killed_name in dead_player_a
                    else dead_player_a
                )
                return self.DOUBLE_DEATH_TIPS["werewolf"].format(
                    dead_player_a=dead_player_a,
                    dead_player_b=dead_player_b,
                    wolf_killed=wolf_killed_name,
                    witch_poisoned=witch_poisoned,
                )
            # 如果没找到刀人记录，返回通用狼人视角
            return self.DOUBLE_DEATH_TIPS["werewolf"].format(
                dead_player_a=dead_player_a,
                dead_player_b=dead_player_b,
                wolf_killed="你们刀的那个",
                witch_poisoned="另一个死者",
            )

        # 好人视角（预言家、猎人、村民）
        return self.DOUBLE_DEATH_TIPS["good"].format(
            dead_player_a=dead_player_a, dead_player_b=dead_player_b
        )

    def _get_special_event_tip(self, player: "Player", room: "GameRoom") -> str:
        """获取特殊事件提示词（平安夜或双死）"""
        # 先检查双死（更重要，确保不会被平安夜覆盖）
        double_death_tip = self._get_double_death_tip(player, room)
        if double_death_tip:
            return double_death_tip

        # 再检查平安夜
        peaceful_tip = self._get_peaceful_night_tip(player, room)
        if peaceful_tip:
            return peaceful_tip

        return ""

    # ==================== 狼人行动 ====================

    async def decide_werewolf_kill(
        self, player: "Player", room: "GameRoom"
    ) -> int | None:
        """AI狼人选择击杀目标"""
        context = self._build_context(player, room)
        role_key = self._get_role_key(player)
        soul_setting = self.ROLE_SOUL_SETTINGS.get(role_key, "")
        tactical_directive = self._get_tactical_directive(player, room)

        prompt = self.ROLE_PROMPTS["werewolf_kill"].format(
            anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
            tactical_directive=tactical_directive,
        )

        response = await self._call_llm(prompt, player)
        if response:
            numbers = re.findall(r"\d+", response)
            if numbers:
                target = int(numbers[0])
                if 1 <= target <= 9:
                    return target
        return None

    async def decide_werewolf_chat(
        self, player: "Player", room: "GameRoom"
    ) -> str | None:
        """AI狼人生成密谋消息"""
        context = self._build_context(player, room)

        prompt = self.ROLE_PROMPTS["werewolf_chat"].format(
            context=context, human_style=self.HUMAN_STYLE_TIPS
        )

        response = await self._call_llm(prompt, player)
        if response:
            return response[:50]
        return None

    # ==================== 预言家行动 ====================

    async def decide_seer_check(self, player: "Player", room: "GameRoom") -> int | None:
        """AI预言家选择验人目标"""
        context = self._build_context(player, room)
        role_key = self._get_role_key(player)
        soul_setting = self.ROLE_SOUL_SETTINGS.get(role_key, "")

        prompt = self.ROLE_PROMPTS["seer_check"].format(
            anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
        )

        response = await self._call_llm(prompt, player)
        if response:
            numbers = re.findall(r"\d+", response)
            if numbers:
                target = int(numbers[0])
                if 1 <= target <= 9:
                    return target
        return None

    # ==================== 女巫行动 ====================

    async def decide_witch_action(
        self,
        player: "Player",
        room: "GameRoom",
        can_save: bool,
        can_poison: bool,
        killed_player_name: str | None = None,
    ) -> tuple[str, int | None]:
        """AI女巫决定用药"""
        context = self._build_context(player, room)
        role_key = self._get_role_key(player)
        soul_setting = self.ROLE_SOUL_SETTINGS.get(role_key, "")

        available_actions = []
        if killed_player_name and can_save:
            available_actions.append(
                f"💊 今晚 {killed_player_name} 被狼人杀害，你可以使用【解药】救他"
            )
        if can_poison:
            available_actions.append("☠️ 你可以使用【毒药】毒死一个人")
        if not available_actions:
            available_actions.append("❌ 你的药都用完了，今晚无法行动")

        prompt = self.ROLE_PROMPTS["witch_action"].format(
            anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
            available_actions="\n".join(available_actions),
        )

        response = await self._call_llm(prompt, player)
        if response:
            response_lower = response.lower()
            if "救" in response_lower or "save" in response_lower:
                return ("save", None)
            elif "毒" in response_lower or "poison" in response_lower:
                numbers = re.findall(r"\d+", response)
                if numbers:
                    return ("poison", int(numbers[0]))
        return ("pass", None)

    # ==================== 猎人行动 ====================

    async def decide_hunter_shoot(
        self, player: "Player", room: "GameRoom"
    ) -> int | None:
        """AI猎人决定开枪目标"""
        context = self._build_context(player, room)
        role_key = self._get_role_key(player)
        soul_setting = self.ROLE_SOUL_SETTINGS.get(role_key, "")

        prompt = self.ROLE_PROMPTS["hunter_shoot"].format(
            anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
        )

        response = await self._call_llm(prompt, player)
        if response:
            if "不开枪" in response or "不开" in response:
                return None
            numbers = re.findall(r"\d+", response)
            if numbers:
                target = int(numbers[0])
                if 1 <= target <= 9:
                    return target
        return None

    # ==================== 白天发言 ====================

    async def generate_speech(
        self, player: "Player", room: "GameRoom", is_pk: bool = False
    ) -> str:
        """AI生成白天发言"""
        context = self._build_context(player, room)
        context += "\n" + self._get_situation_awareness(room)

        # 检查特殊事件（平安夜或双死），追加提示词
        special_event_tip = self._get_special_event_tip(player, room)
        if special_event_tip:
            context += "\n" + special_event_tip

        # 添加战术指令（绝境局/狼人收割等）
        tactical_directive = self._get_tactical_directive(player, room)
        if tactical_directive:
            context += "\n" + tactical_directive

        # 添加对跳辩论提示词（如有对跳）
        duel_context = self._get_duel_context(player, room)
        if duel_context:
            context += "\n" + duel_context

        # 添加玩家行为分析（抿狼技巧）
        behavior_analysis = self._get_behavior_analysis_prompt(player, room)
        if behavior_analysis:
            context += "\n" + behavior_analysis

        role_key = self._get_role_key(player)
        soul_setting = self.ROLE_SOUL_SETTINGS.get(role_key, "")
        personality = self._get_player_personality(player)

        if is_pk:
            pk_tips = self.PK_TIPS.get(role_key, self.PK_TIPS["villager"])
            prompt = self.ROLE_PROMPTS["pk_speech"].format(
                anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
                soul_setting=soul_setting,
                personality=personality,
                context=context,
                pk_tips=pk_tips,
                human_style=self.HUMAN_STYLE_TIPS,
            )
        else:
            # 动态调整村民提示词：首日发言 vs 正常发言
            if role_key == "villager" and player.ai_context.current_round == 1:
                # 首日村民发言提示（第一天信息量少，避免过度推理）
                speech_tips = """【👨‍🌾 村民首日发言】
⚠️ 这是第一天，信息量较少，不要过度推理或编造不存在的信息！

🗣️ 首日发言建议：
1. 如果还没有人发言：简单表态，等待信息
2. 如果已有少量发言：简单评价，不要过度分析
3. 如果有预言家跳出：可以表态支持或怀疑，但要基于实际发言
4. 严禁编造"昨天"、"前一天"等虚假信息

💡 记住：根据已有的发言内容发言，不要分析不存在的事情！"""
            else:
                speech_tips = self.SPEECH_TIPS.get(
                    role_key, self.SPEECH_TIPS["villager"]
                )

            prompt = self.ROLE_PROMPTS["day_speech"].format(
                anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
                soul_setting=soul_setting,
                personality=personality,
                context=context,
                speech_tips=speech_tips,
                human_style=self.HUMAN_STYLE_TIPS,
            )

        response = await self._call_llm(prompt, player)
        if response:
            # 清理可能的前缀
            response = re.sub(
                r"^[\[【]?(发言|说话|speech)[\]】]?[：:]\s*",
                "",
                response,
                flags=re.IGNORECASE,
            )
            # 限制发言长度（300字符，避免刷屏但保持完整表达）
            return response[:300]

        # 默认发言
        defaults = [
            "我先听听大家怎么说吧",
            "目前信息太少了，我再观察一下",
            "emmm 我暂时没什么想法",
        ]
        return random.choice(defaults)

    # ==================== 投票 ====================

    async def decide_vote(
        self,
        player: "Player",
        room: "GameRoom",
        is_pk: bool = False,
        pk_candidates: list[str] = None,
    ) -> tuple[str, int | None]:
        """AI生成投票决策"""
        context = self._build_context(player, room)
        context += "\n" + self._get_situation_awareness(room)

        # 检查特殊事件（平安夜或双死），追加提示词
        special_event_tip = self._get_special_event_tip(player, room)
        if special_event_tip:
            context += "\n" + special_event_tip

        # 添加战术指令（绝境局/狼人收割等）
        tactical_directive = self._get_tactical_directive(player, room)
        if tactical_directive:
            context += "\n" + tactical_directive

        # 添加玩家行为分析（抿狼技巧）
        behavior_analysis = self._get_behavior_analysis_prompt(player, room)
        if behavior_analysis:
            context += "\n" + behavior_analysis

        role_key = self._get_role_key(player)
        soul_setting = self.ROLE_SOUL_SETTINGS.get(role_key, "")
        vote_tips = self.VOTE_TIPS.get(role_key, self.VOTE_TIPS["villager"])

        prompt = self.ROLE_PROMPTS["day_vote"].format(
            anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
            vote_tips=vote_tips,
            human_style=self.HUMAN_STYLE_TIPS,
        )

        response = await self._call_llm(prompt, player)

        speech = ""
        vote_target = None

        if response:
            # 解析发言
            speech_match = re.search(
                r"\[发言\]\s*(.+?)(?=\[投票\]|$)", response, re.DOTALL
            )
            if speech_match:
                speech = speech_match.group(1).strip()[:30]

            # 解析投票
            vote_match = re.search(r"\[投票\]\s*(\d+|弃票)", response)
            if vote_match:
                vote_str = vote_match.group(1)
                if vote_str != "弃票":
                    try:
                        vote_target = int(vote_str)
                        if not (1 <= vote_target <= 9):
                            vote_target = None
                    except ValueError:
                        pass

            # 如果没有找到格式化的内容，尝试直接提取
            if not speech and not vote_target:
                numbers = re.findall(r"\d+", response)
                if numbers:
                    vote_target = int(numbers[0])
                    if not (1 <= vote_target <= 9):
                        vote_target = None
                speech = response[:30] if len(response) <= 30 else ""

        return (speech, vote_target)

    # ==================== 遗言 ====================

    async def generate_last_words(self, player: "Player", room: "GameRoom") -> str:
        """AI生成遗言"""
        context = self._build_context(player, room)
        role_key = self._get_role_key(player)
        role_name = player.role.display_name if player.role else "玩家"

        last_words_tips = self.LAST_WORDS_TIPS.get(
            role_key, self.LAST_WORDS_TIPS["villager"]
        )

        # 预言家特殊处理：附加验人结果
        if role_key == "seer" and player.ai_context and player.ai_context.seer_results:
            results = [
                f"{r['target']}是{'狼' if r['is_werewolf'] else '金水'}"
                for r in player.ai_context.seer_results
            ]
            last_words_tips += (
                f"\n\n🔮 【重要】你的查验记录：{'; '.join(results)}\n务必全部公布出来！"
            )

        prompt = self.ROLE_PROMPTS["last_words"].format(
            anti_hallucination=self.ANTI_HALLUCINATION_PROTOCOL,
            context=context,
            role_name=role_name,
            last_words_tips=last_words_tips,
            human_style=self.HUMAN_STYLE_TIPS,
        )

        response = await self._call_llm(prompt, player)
        if response:
            return response[:100]

        return "我没什么好说的了，祝大家好运。"

    # ==================== 上下文更新 ====================

    def initialize_ai_context(self, player: "Player", room: "GameRoom") -> None:
        """初始化AI玩家的游戏上下文"""
        from ..models.ai_player import AIPlayerContext

        if not player.is_ai:
            return

        # 创建上下文对象
        ctx = AIPlayerContext()
        ctx.player_number = player.number
        ctx.role_name = player.role.display_name if player.role else "未知"
        ctx.is_werewolf = player.role and player.role.value == "werewolf"
        ctx.current_round = room.current_round
        ctx.current_phase = self._get_phase_description(room)

        # 初始化存活玩家列表
        alive_list = [p.display_name for p in room.get_alive_players()]
        ctx.update_alive_players(alive_list, [])

        # 狼人：记录队友
        if ctx.is_werewolf:
            teammates = [
                w.display_name for w in room.get_alive_werewolves() if w.id != player.id
            ]
            ctx.werewolf_teammates = teammates

        # 绑定到玩家
        player.ai_context = ctx

        logger.info(f"[狼人杀AI] 初始化 {player.name} 的上下文: {ctx.role_name}")

    def update_ai_context(self, player: "Player", room: "GameRoom") -> None:
        """更新AI玩家的游戏上下文"""
        if not player.is_ai or not player.ai_context:
            return

        ctx = player.ai_context
        ctx.current_round = room.current_round
        ctx.current_phase = self._get_phase_description(room)

        alive_list = [p.display_name for p in room.get_alive_players()]
        dead_list = [p.display_name for p in room.players.values() if not p.is_alive]
        ctx.update_alive_players(alive_list, dead_list)

        if player.role and player.role.value == "witch":
            ctx.witch_antidote_used = room.witch_state.antidote_used
            ctx.witch_poison_used = room.witch_state.poison_used
            if room.last_killed_id:
                killed = room.get_player(room.last_killed_id)
                ctx.last_killed_player = killed.display_name if killed else None

    def _get_phase_description(self, room: "GameRoom") -> str:
        """获取当前阶段的人类可读描述"""
        from ..models import GamePhase

        phase = room.phase
        round_num = room.current_round

        # 首日发言阶段特殊描述（明确首日，防止AI产生虚假记忆）
        if round_num == 1 and phase == GamePhase.DAY_SPEAKING:
            return "第1天白天（首日发言）- 昨晚只分配了身份，今天是第一次发言，没有任何前置信息"

        phase_map = {
            GamePhase.NIGHT_WOLF: f"第{round_num}天夜晚 - 狼人行动阶段",
            GamePhase.NIGHT_SEER: f"第{round_num}天夜晚 - 预言家验人阶段",
            GamePhase.NIGHT_WITCH: f"第{round_num}天夜晚 - 女巫行动阶段",
            GamePhase.DAY_SPEAKING: f"第{round_num}天白天 - 发言阶段",
            GamePhase.DAY_VOTE: f"第{round_num}天白天 - 投票阶段",
            GamePhase.DAY_PK: f"第{round_num}天白天 - PK发言阶段",
            GamePhase.LAST_WORDS: f"第{round_num}天 - 遗言阶段",
        }

        return phase_map.get(phase, f"第{round_num}天")

    def clear_player_data(self, player_id: str) -> None:
        """清理玩家数据"""
        self._retry_counts.pop(player_id, None)
        self._player_personalities.pop(player_id, None)
