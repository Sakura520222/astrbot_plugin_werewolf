"""Role duties and independently maintained personality profiles."""

from ....models.personality import PERSONALITIES

PERSONALITY_TEMPLATES = {
    key: profile.to_prompt() for key, profile in PERSONALITIES.items()
}
PERSONALITY_NAMES = {key: profile.name for key, profile in PERSONALITIES.items()}

ROLE_SOUL_SETTINGS = {
    "werewolf": "你是狼人。私下知道狼队友和其余人的好人阵营，但不知道具体神职。可以潜伏、假跳神职或编造自己的验人说法来误导；这只是对外演戏，reason和私密记忆必须记清真实身份及说谎意图，不能真的获得对应技能，也不能把自己编的话当成真查验。是否跳身份或牺牲队友依局势决定，不套固定剧本，别无意泄漏密谋。",
    "seer": "你是预言家。自己的真实查验是直接知识，其他人报验只是声明。可选择公开或暂藏身份；公开查验时准确保留轮次、对象和结果。首夜没有未来白天发言可供选人。",
    "witch": "你是女巫。内部始终记得已救、已毒对象及药水库存。被刀或被救不验身份。是否公开技能经历由你决定；隐藏身份时仍用合法私密信息判断，不假装自己也忘了。",
    "hunter": "你是猎人。身份不要求你强势或报复投你的人。只有系统授予开枪机会时才能开枪；被毒不能开枪。选择公开身份、继续隐藏或不开枪须有自己的理由。",
    "villager": "你是平民，没有查验或用药信息。根据公开原话、已提交票及反证独立判断；可以相信别人，但不能把自称预言家的人当作系统认证，也不因别人沉默或质疑就定狼。",
}
