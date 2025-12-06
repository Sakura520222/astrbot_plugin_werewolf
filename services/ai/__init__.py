"""AI模块 - 模块化的AI玩家服务

这个模块将原来的单文件 ai_player_service.py (2000+行)
重构为职责清晰的多模块结构：

结构:
  ai/
  ├── prompts/          # 提示词模块
  │   ├── base.py       # 基础协议（防幻觉、语言风格）
  │   ├── roles.py      # 角色灵魂设定
  │   ├── strategies.py # 发言/投票/PK/遗言策略
  │   ├── templates.py  # 场景化提示词模板
  │   ├── events.py     # 平安夜、双死等事件
  │   └── tactics.py    # 战术分析、对跳辩论
  ├── actions/          # 行动决策模块
  │   ├── base.py       # 行动基类
  │   ├── werewolf.py   # 狼人行动
  │   ├── seer.py       # 预言家行动
  │   ├── witch.py      # 女巫行动
  │   ├── hunter.py     # 猎人行动
  │   ├── speech.py     # 发言生成
  │   └── vote.py       # 投票决策
  ├── context/          # 上下文模块
  │   ├── builder.py    # 上下文构建
  │   └── analyzer.py   # 局势分析、行为分析
  ├── validators.py     # 统一验证器（防止操作死亡玩家）
  └── service.py        # 主服务（整合入口）

使用:
  from .ai import AIPlayerService
  service = AIPlayerService(context)
"""

from .service import AIPlayerService

__all__ = ['AIPlayerService']
