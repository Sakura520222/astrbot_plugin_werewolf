"""Small stage-specific tasks assembled after shared rules and evidence."""

ROLE_PROMPTS = {
    "werewolf_kill": "狼人选刀：结合自己知道的队友、真实密谋与公开线索选择合法目标，或明确弃票不提交刀人目标（abstain=true、target_number=null）。全部弃票则当夜空刀。首夜可按偏好选人，不用编号推定神职，也不能引用尚未发生的发言。选刀意图不同于最终集体目标。",
    "werewolf_chat": "狼人密谋：向队友提出一个具体目标或配合建议，可以不同意队友；约15–60字，需要时完整说明。不替别人编造已决定的行动。",
    "seer_check": "预言家选验：根据目前已有信息及未知之处选择。首夜没有白天观察，允许承认初始选择缺少信息；不为随机或偏好选人编造事后理由。",
    "witch_action": "女巫行动：核对当夜被刀提示、真实药水库存及过去操作；可救、毒或不操作，同一夜只做一种。被刀者和被救者不自动为好人，别人的查杀只是声明。",
    "hunter_shoot": "猎人选择：系统已授予开枪机会才可射击。按证据选择合法目标或明确不开枪，不因被票出就机械报复。",
    "day_speech": "普通发言：先形成自己的判断和行动意向，再用人格语气表达一个重点，通常25–100字，低表达人格可更短。被直接查杀或质疑时可50–150字；不要轮流复述相同论点。",
    "pk_speech": "PK发言：优先回应针对自己的实际质疑，必要时解释真实历史票和行动。通常50–150字，可按人格及必要信息调整；不强制攻击对手。",
    "day_vote": "投票：普通投票和PK投票都可选择合法目标或主动弃票（abstain=true、target_number=null）；弃票算完成投票但不计入目标票数，全部弃票无人出局。先决定行动，再写一致的公开理由，通常15–60字。不能复制整段白天发言。若改变此前公开意向，填写change_reason说明新依据或新解释；解析失败不等于主动弃票。",
    "last_words": "遗言：当前确有遗言授权。通常30–120字，真实神职信息必须完整，可为此更长。不要凭空再获得技能或遗言机会。",
}

DECISION_PROTOCOL = """【输出协议】
只输出一个JSON对象，无Markdown。先填写intent和reason决定行动，再填写speech作公开表达。
intent：简短行动意图；reason：内部依据（不会公开）；speech：本阶段要发出的自然语言，不含内部分析和未决定公开的秘密。
evidence_ids：使用的证据编号列表，如["S1","V1"]；没有已有证据可为[]，不得捏造编号。
target_number：选择或公开倾向的合法玩家编号，无目标为null；abstain：是否明确放弃本次行动，布尔值。
action：当前行动名称；change_reason：改动此前立场或公开投票意向的新依据/新解释，没有改变可为空。
assessments：个人判断更新列表，可为空；每项含player_number、suspicion(0–10)、confidence(0–1)、reason、evidence_ids、counter_evidence_ids。
只更新有具体依据的判断，反复听到同一观点不增加独立证据。个人判断是推测，不能写成系统认证。
发言、PK、遗言和密谋必须有speech；投票必须同时给speech、target_number和abstain。
选刀/查验/开枪/用药不需要公开speech，但仍须给出intent、reason和合法目标；放弃用abstain=true、target_number=null。
不得把模型内部依据作为公开发言返回。所有篇幅只是提示词软目标，回复会完整保留。"""
