"""Event guidance that never manufactures identity or death-cause facts."""

from .roles import PERSONALITY_NAMES

PEACEFUL_NIGHT_TIPS = {
    "good": "公开事实只有昨夜无人死亡；不知道确切获救对象，不要求女巫统一跳身份。",
    "werewolf": "公开事实是平安夜。结合自己记录的实际选刀判断，女巫用药仍非直接已知；公聊不自动透露目标。",
    "witch": "平安夜与自己的真实操作分开核对：只有技能记录中的获救对象才是你知道的救人事实；是否公开由你决定。",
}

DOUBLE_DEATH_TIPS = {
    "good": "公开公告有多人死亡，但没有公布具体死因或身份。可以讨论可能原因，不能断定谁被毒、被毒者是狼。",
    "werewolf": "结合狼队已知刀人记录分析多人死亡；推导必须标为推断，不自动获得女巫的操作或他人具体神职。",
    "witch": "结合自己的实际被刀提示和已提交用药记录分析多人死亡；不能因为自己毒了某人就确定其阵营。是否公开用药自行决定。",
    "hunter_dead": "能否开枪、是否有遗言只看系统授权；不能根据死亡人数自行创建技能或遗言机会。",
}
__all__ = ["PEACEFUL_NIGHT_TIPS", "DOUBLE_DEATH_TIPS", "PERSONALITY_NAMES"]
