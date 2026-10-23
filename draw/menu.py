"""狼人杀菜单图片生成"""

import math

from PIL import Image, ImageDraw

from ..utils import get_command_prefix
from .gradient_utils import create_vertical_gradient
from .styles import (
    COLOR_BACKGROUND_BOT,
    COLOR_BACKGROUND_TOP,
    COLOR_BLOOD_MOON,
    COLOR_CARD_BG,
    COLOR_CARD_BORDER,
    COLOR_CMD,
    COLOR_CMD_DAY,
    COLOR_CMD_NIGHT,
    COLOR_HUNTER,
    COLOR_MOONLIGHT,
    COLOR_SEER,
    COLOR_TEXT_DIM,
    COLOR_TITLE,
    COLOR_WEREWOLF,
    COLOR_WITCH,
    load_font,
)


def draw_menu_image(total_players: int = 9) -> Image.Image:
    """
    生成狼人杀帮助菜单图片

    Args:
        total_players: 游戏人数，用于显示在帮助中

    Returns:
        PIL Image 对象
    """
    width = 800

    # 加载字体
    title_font = load_font(36)
    section_font = load_font(24)
    cmd_font = load_font(17)
    desc_font = load_font(14)

    # 测量文本辅助函数
    _measure_img = Image.new("RGB", (10, 10), COLOR_BACKGROUND_BOT)
    _measure_draw = ImageDraw.Draw(_measure_img)

    def measure_text_size(text, font):
        bbox = _measure_draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def draw_card(draw, x0, y0, x1, y1, radius=12, highlight_color=None):
        """绘制圆角卡片"""
        shadow_offset = 3
        draw.rounded_rectangle(
            [
                x0 + shadow_offset,
                y0 + shadow_offset,
                x1 + shadow_offset,
                y1 + shadow_offset,
            ],
            radius,
            fill=(0, 0, 0, 60),
        )
        border_color = highlight_color if highlight_color else COLOR_CARD_BORDER
        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius, fill=COLOR_CARD_BG, outline=border_color, width=2
        )

    def draw_section(
        draw,
        title,
        cmds,
        y_start,
        cols=3,
        cmd_color=COLOR_CMD,
        title_color=COLOR_MOONLIGHT,
    ):
        """绘制命令章节"""
        title_x = 50
        draw.text(
            (title_x, y_start), title, fill=title_color, font=section_font, anchor="lm"
        )
        w, h = measure_text_size(title, section_font)

        underline_y = y_start + h // 2 + 8
        draw.line(
            [(title_x, underline_y), (title_x + w, underline_y)],
            fill=title_color,
            width=2,
        )

        y = y_start + h // 2 + 25
        card_w = (width - 60) // cols
        card_h = 80
        pad = 12

        for idx, (cmd, desc, highlight) in enumerate(cmds):
            col = idx % cols
            row = idx // cols
            x0 = 30 + col * card_w
            y0 = y + row * (card_h + pad)
            x1 = x0 + card_w - 10
            y1 = y0 + card_h

            draw_card(draw, x0, y0, x1, y1, highlight_color=highlight)

            cx = (x0 + x1) // 2
            draw.text((cx, y0 + 16), cmd, fill=cmd_color, font=cmd_font, anchor="mt")

            desc_lines = desc.split("\n") if "\n" in desc else [desc]
            for i, line in enumerate(desc_lines):
                draw.text(
                    (cx, y0 + 42 + i * 16),
                    line,
                    fill=COLOR_TEXT_DIM,
                    font=desc_font,
                    anchor="mt",
                )

        rows = math.ceil(len(cmds) / cols)
        return y + rows * (card_h + pad) + 30

    # 命令数据 - (命令, 描述, 高亮颜色)
    # 动态获取命令前缀
    prefix = get_command_prefix()
    basic_cmds = [
        (f"{prefix}创建房间", "创建游戏房间", None),
        (f"{prefix}加入房间", "加入游戏", None),
        (f"{prefix}开始游戏", "开始游戏\n（房主）", None),
        (f"{prefix}查角色", "私聊查看\n自己角色", None),
        (f"{prefix}游戏状态", "查看当前\n游戏状态", None),
        (f"{prefix}结束游戏", "强制结束\n（房主）", None),
    ]

    night_cmds = [
        (f"{prefix}办掉 编号", "狼人办掉目标", COLOR_WEREWOLF),
        (f"{prefix}密谋 消息", "狼人密谋", COLOR_WEREWOLF),
        (f"{prefix}弃票", "狼人不提交刀票\n全员弃票则空刀", COLOR_WEREWOLF),
        (f"{prefix}验人 编号", "预言家查验", COLOR_SEER),
        (f"{prefix}救人", "女巫救人", COLOR_WITCH),
        (f"{prefix}毒人 编号", "女巫毒人", COLOR_WITCH),
        (f"{prefix}不操作", "女巫跳过", COLOR_WITCH),
        (f"{prefix}开枪 编号", "猎人开枪", COLOR_HUNTER),
    ]

    day_cmds = [
        (f"{prefix}发言完毕", "结束发言", None),
        (f"{prefix}遗言完毕", "结束遗言", None),
        (f"{prefix}开始投票", "跳过发言\n（房主）", None),
        (f"{prefix}投票 编号", "投票放逐", None),
        (f"{prefix}弃票", "白天或PK弃票\n计为已投票", None),
    ]

    # 计算高度
    def section_delta(item_count: int, cols: int) -> int:
        rows = math.ceil(item_count / cols) if item_count > 0 else 0
        _, h = measure_text_size("标题", section_font)
        card_h = 80
        pad = 12
        return (h // 2 + 25) + rows * (card_h + pad) + 30

    y0_est = 90
    y0_est += section_delta(len(basic_cmds), 3)
    y0_est += section_delta(len(night_cmds), 3)
    y0_est += section_delta(len(day_cmds), 3)
    footer_y_est = y0_est + 80
    final_height = footer_y_est + 60

    # 创建画布
    image = create_vertical_gradient(
        width, final_height, COLOR_BACKGROUND_TOP, COLOR_BACKGROUND_BOT
    )
    draw = ImageDraw.Draw(image, "RGBA")

    # 绘制标题
    title_y = 50
    draw.text(
        (width // 2, title_y),
        "🐺 狼人杀 · 暗夜狼嚎",
        fill=COLOR_TITLE,
        font=title_font,
        anchor="mm",
    )

    # 绘制各个部分
    y0 = 90
    y0 = draw_section(draw, "📋 基础命令", basic_cmds, y0, cols=3, cmd_color=COLOR_CMD)
    y0 = draw_section(
        draw,
        "🌙 夜晚命令（私聊机器人）",
        night_cmds,
        y0,
        cols=3,
        cmd_color=COLOR_CMD_NIGHT,
        title_color=COLOR_BLOOD_MOON,
    )
    y0 = draw_section(
        draw,
        "☀️ 白天命令（群聊）",
        day_cmds,
        y0,
        cols=3,
        cmd_color=COLOR_CMD_DAY,
        title_color=COLOR_MOONLIGHT,
    )

    # 游戏规则提示
    rules_y = y0 + 10
    rules = [
        f"💡 使用编号（1-{total_players}号）指定目标玩家",
        "🎭 遗言规则：首夜被杀有遗言，被毒无遗言",
        "🔫 猎人：被杀/投票出局可开枪，被毒不能开枪",
        "🏆 胜负：狼人出局=好人胜 | 好人≤狼人 或 神全灭=狼人胜",
    ]
    for i, rule in enumerate(rules):
        draw.text(
            (width // 2, rules_y + i * 22),
            rule,
            fill=COLOR_TEXT_DIM,
            font=desc_font,
            anchor="mm",
        )

    # 底部装饰
    footer_y = rules_y + len(rules) * 22 + 15
    draw.text(
        (width // 2, footer_y),
        "🌕 月圆之夜，狼人觉醒...",
        fill=COLOR_BLOOD_MOON,
        font=desc_font,
        anchor="mm",
    )

    # 裁剪到实际高度
    final_height = footer_y + 25
    image = image.crop((0, 0, width, final_height))

    return image
