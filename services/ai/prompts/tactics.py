"""Optional tactical considerations, never assertions of hidden truth."""

SITUATION_TEMPLATE = (
    "当前第{round_number}轮，存活{alive_count}/{total_players}人；具体存活阵营不公开。"
)
TACTICAL_DIRECTIVES = {
    "wolf_advantage": "根据已知狼队票数评估胜负，不因优势就强制摊牌。",
    "wolf_disadvantage": "独狼可选择潜伏、争取信任或冒险引导，依据实际公开处境。",
    "wolf_teammate_exposed": "队友遭到的是公开指控，发言者真实身份未知；权衡支援、切割或另选目标。",
    "good_confused": "公开线索仍有歧义，比较各解释，不将混乱等同于任何具体神职已死亡。",
    "normal": "选择与既有经历相容的策略，不必每次采用相同战术。",
    "good_desperate": "认真核实关键证据，不因局势紧张就无依据推翻判断。",
    "wolf_final_push": "根据已知票数评估推进风险，不强制分票或跟票。",
}
DUEL_CONTEXT_TEMPLATE = {
    "attacker": "你和{opponent}都有明确自称预言家的原话。比较声明与真实记录，不能仅凭对跳确定对方阵营。",
    "observer": "{player_a}和{player_b}都有明确自称预言家的原话。可能一真一假或都是假的；自称数量不是身份认证。",
}
BEHAVIOR_ANALYSIS_TIPS = (
    "只描述可追溯的原话及已提交操作；短发言、语气和质疑本身不构成身份事实。"
)
BEHAVIOR_TAG_DEFINITIONS = {
    "自称预言家": "本人明确认领预言家身份或自己的查验，不包含转述、假设或单纯反驳。",
    "公开投票": "系统已提交的票，不等同于讨论期间的意向。",
}
