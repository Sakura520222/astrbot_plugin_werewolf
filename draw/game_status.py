"""游戏状态图片生成"""

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from .gradient_utils import create_vertical_gradient
from .styles import (
    COLOR_ALIVE,
    COLOR_BACKGROUND_BOT,
    COLOR_BACKGROUND_TOP,
    COLOR_BLOOD_MOON,
    COLOR_CARD_BG,
    COLOR_CARD_BORDER,
    COLOR_DAY,
    COLOR_DEAD,
    COLOR_MOONLIGHT,
    COLOR_NIGHT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_LIGHT,
    COLOR_TITLE,
    load_font,
)

if TYPE_CHECKING:
    pass


def draw_game_status(
    phase: str,
    day_count: int,
    players: list[dict],
    alive_count: int,
    total_count: int,
) -> Image.Image:
    """
    生成游戏状态图片

    Args:
        phase: 当前阶段名称
        day_count: 天数
        players: 玩家列表 [{"number": 1, "name": "xxx", "alive": True}, ...]
        alive_count: 存活人数
        total_count: 总人数

    Returns:
        PIL Image 对象
    """
    width = 600

    # 加载字体
    title_font = load_font(28)
    subtitle_font = load_font(18)
    text_font = load_font(16)
    number_font = load_font(24)
    small_font = load_font(14)

    # 计算高度
    rows = (len(players) + 2) // 3  # 每行3个玩家
    player_card_height = 70
    player_section_height = rows * (player_card_height + 10) + 20
    base_height = 180 + player_section_height + 60

    # 创建画布
    image = create_vertical_gradient(
        width, base_height, COLOR_BACKGROUND_TOP, COLOR_BACKGROUND_BOT
    )
    draw = ImageDraw.Draw(image, "RGBA")

    # 判断是白天还是夜晚
    is_night = "夜晚" in phase or "night" in phase.lower()
    phase_color = COLOR_NIGHT if is_night else COLOR_DAY
    phase_emoji = "🌙" if is_night else "☀️"

    # 标题
    y = 35
    draw.text(
        (width // 2, y),
        "🐺 狼人杀 · 游戏状态",
        fill=COLOR_TITLE,
        font=title_font,
        anchor="mm",
    )

    # 阶段信息
    y += 45
    phase_text = f"{phase_emoji} 第 {day_count} 天 · {phase}"
    draw.text(
        (width // 2, y), phase_text, fill=phase_color, font=subtitle_font, anchor="mm"
    )

    # 存活统计
    y += 35
    alive_text = f"存活人数：{alive_count}/{total_count}"
    draw.text(
        (width // 2, y), alive_text, fill=COLOR_ALIVE, font=text_font, anchor="mm"
    )

    # 分割线
    y += 30
    line_margin = 50
    draw.line(
        [(line_margin, y), (width - line_margin, y)], fill=COLOR_CARD_BORDER, width=1
    )

    # 玩家列表标题
    y += 20
    draw.text(
        (width // 2, y),
        "📋 玩家列表",
        fill=COLOR_MOONLIGHT,
        font=text_font,
        anchor="mm",
    )

    # 玩家卡片
    y += 25
    card_width = 170
    card_height = player_card_height
    card_margin = 15
    start_x = (width - (card_width * 3 + card_margin * 2)) // 2

    for idx, player in enumerate(players):
        col = idx % 3
        row = idx // 3

        x0 = start_x + col * (card_width + card_margin)
        y0 = y + row * (card_height + 10)
        x1 = x0 + card_width
        y1 = y0 + card_height

        # 根据存活状态选择颜色
        is_alive = player.get("alive", True)
        if is_alive:
            bg_color = COLOR_CARD_BG
            border_color = COLOR_ALIVE
            text_color = COLOR_TEXT_LIGHT
            status_text = "存活"
            status_color = COLOR_ALIVE
        else:
            bg_color = (30, 30, 35, 200)
            border_color = COLOR_DEAD
            text_color = COLOR_DEAD
            status_text = "出局"
            status_color = COLOR_DEAD

        # 绘制卡片
        draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=8,
            fill=bg_color,
            outline=border_color,
            width=1,
        )

        # 玩家编号
        number = player.get("number", idx + 1)
        cx = (x0 + x1) // 2
        draw.text(
            (cx, y0 + 18), f"{number}号", fill=text_color, font=number_font, anchor="mm"
        )

        # 玩家名称（截断过长的名字）
        name = player.get("name", f"玩家{number}")
        if len(name) > 6:
            name = name[:5] + "..."
        draw.text((cx, y0 + 42), name, fill=text_color, font=small_font, anchor="mm")

        # 状态标签
        draw.text(
            (cx, y0 + 58), status_text, fill=status_color, font=small_font, anchor="mm"
        )

    # 底部提示
    footer_y = y + rows * (card_height + 10) + 25
    tip_text = "🎮 使用 /狼人杀帮助 查看完整命令"
    draw.text(
        (width // 2, footer_y),
        tip_text,
        fill=COLOR_TEXT_DIM,
        font=small_font,
        anchor="mm",
    )

    # 裁剪到实际高度
    final_height = footer_y + 25
    image = image.crop((0, 0, width, final_height))

    return image


def draw_vote_result(
    vote_data: list[dict],
    exiled_player: str | None = None,
    is_pk: bool = False,
) -> Image.Image:
    """
    生成投票结果图片

    Args:
        vote_data: 投票数据 [{"name": "xxx", "votes": 3, "voters": ["a", "b", "c"]}, ...]
        exiled_player: 被放逐的玩家名称
        is_pk: 是否是PK投票

    Returns:
        PIL Image 对象
    """
    width = 550

    # 加载字体
    title_font = load_font(26)
    text_font = load_font(16)
    small_font = load_font(14)
    vote_font = load_font(20)

    # 计算高度
    base_height = 120 + len(vote_data) * 55 + 80

    # 创建画布
    image = create_vertical_gradient(
        width, base_height, COLOR_BACKGROUND_TOP, COLOR_BACKGROUND_BOT
    )
    draw = ImageDraw.Draw(image, "RGBA")

    # 标题
    y = 35
    title = "⚔️ PK投票结果" if is_pk else "🗳️ 投票结果"
    draw.text((width // 2, y), title, fill=COLOR_TITLE, font=title_font, anchor="mm")

    # 按票数排序
    sorted_data = sorted(vote_data, key=lambda x: x.get("votes", 0), reverse=True)
    max_votes = sorted_data[0].get("votes", 0) if sorted_data else 0

    # 投票条
    y += 50
    bar_margin = 60
    bar_max_width = width - bar_margin * 2 - 100

    for item in sorted_data:
        name = item.get("name", "???")
        votes = item.get("votes", 0)
        voters = item.get("voters", [])

        # 名称
        draw.text(
            (bar_margin, y), name, fill=COLOR_TEXT_LIGHT, font=text_font, anchor="lm"
        )

        # 投票条
        bar_x = bar_margin + 80
        bar_y = y - 8
        bar_height = 16

        if max_votes > 0:
            bar_width = int((votes / max_votes) * bar_max_width)
        else:
            bar_width = 0

        # 背景条
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + bar_max_width, bar_y + bar_height],
            radius=4,
            fill=(50, 50, 60),
        )

        # 实际投票条
        if bar_width > 0:
            bar_color = COLOR_BLOOD_MOON if name == exiled_player else COLOR_MOONLIGHT
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
                radius=4,
                fill=bar_color,
            )

        # 票数
        draw.text(
            (bar_x + bar_max_width + 10, y),
            f"{votes}票",
            fill=COLOR_TEXT_LIGHT,
            font=vote_font,
            anchor="lm",
        )

        # 投票者（如果有）
        if voters:
            voters_text = "← " + ", ".join(voters[:5])
            if len(voters) > 5:
                voters_text += f" +{len(voters) - 5}"
            y += 20
            draw.text(
                (bar_x, y),
                voters_text,
                fill=COLOR_TEXT_DIM,
                font=small_font,
                anchor="lm",
            )

        y += 35

    # 结果
    y += 15
    if exiled_player:
        result_text = f"💀 {exiled_player} 被放逐出局"
        result_color = COLOR_BLOOD_MOON
    else:
        result_text = "⚖️ 平票，无人出局"
        result_color = COLOR_MOONLIGHT

    draw.text(
        (width // 2, y), result_text, fill=result_color, font=text_font, anchor="mm"
    )

    # 裁剪到实际高度
    final_height = y + 30
    image = image.crop((0, 0, width, final_height))

    return image


def draw_night_result(
    killed_player: str | None = None,
    poisoned_player: str | None = None,
    saved: bool = False,
) -> Image.Image:
    """
    生成夜晚结算图片

    Args:
        killed_player: 被狼人杀的玩家
        poisoned_player: 被女巫毒死的玩家
        saved: 是否被救活

    Returns:
        PIL Image 对象
    """
    width = 500
    height = 200

    # 加载字体
    title_font = load_font(26)
    text_font = load_font(18)

    # 创建画布
    image = create_vertical_gradient(
        width, height, COLOR_BACKGROUND_TOP, COLOR_BACKGROUND_BOT
    )
    draw = ImageDraw.Draw(image, "RGBA")

    # 标题
    y = 35
    draw.text(
        (width // 2, y), "🌅 天亮了", fill=COLOR_TITLE, font=title_font, anchor="mm"
    )

    # 结果
    y += 50
    deaths = []

    if killed_player and not saved:
        deaths.append(f"🐺 {killed_player} 被狼人袭击")
    elif killed_player and saved:
        draw.text(
            (width // 2, y),
            "💊 昨晚有人被救活了",
            fill=COLOR_ALIVE,
            font=text_font,
            anchor="mm",
        )
        y += 30

    if poisoned_player:
        deaths.append(f"☠️ {poisoned_player} 中毒身亡")

    if deaths:
        for death in deaths:
            draw.text(
                (width // 2, y),
                death,
                fill=COLOR_BLOOD_MOON,
                font=text_font,
                anchor="mm",
            )
            y += 30
    elif not killed_player:
        draw.text(
            (width // 2, y),
            "🌙 昨晚是平安夜，无人死亡",
            fill=COLOR_ALIVE,
            font=text_font,
            anchor="mm",
        )

    # 裁剪到实际高度
    final_height = y + 30
    image = image.crop((0, 0, width, final_height))

    return image
