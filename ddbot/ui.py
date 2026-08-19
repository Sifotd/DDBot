from __future__ import annotations

from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .models import Delivery, Post, ScheduledPush

STATUS_LABELS = {
    "published": "已发布",
    "modified": "已修改",
    "deleted": "已删除",
    "partial": "部分成功",
    "failed": "失败",
}


def valid_button_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https", "tg"} and bool(
            parsed.netloc or parsed.scheme == "tg"
        )
    except ValueError:
        return False


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ 创建内容", callback_data="new")],
            [InlineKeyboardButton(text="📋 已发布内容", callback_data="posts:0")],
            [InlineKeyboardButton(text="⚙️ 当前频道", callback_data="channels")],
        ]
    )


def button_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ 使用频道按钮模板", callback_data="draft:button:template"
                )
            ],
            [InlineKeyboardButton(text="➕ 使用一个自定义按钮", callback_data="draft:button:add")],
            [InlineKeyboardButton(text="不使用按钮", callback_data="draft:button:skip")],
            [InlineKeyboardButton(text="❌ 取消", callback_data="draft:cancel")],
        ]
    )


def channel_choice(channels: dict[str, str]) -> InlineKeyboardMarkup:
    names = {
        "eai": "Alice EAI（英文）",
        "korean": "Alice Korean Bet（韩文）",
        "traditional": "Alice（繁體中文）",
    }
    rows = [
        [
            InlineKeyboardButton(
                text=names.get(key, username), callback_data=f"draft:target:{key}"
            )
        ]
        for key, username in channels.items()
    ]
    if len(channels) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"全部 {len(channels)} 个频道", callback_data="draft:target:all"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="❌ 取消", callback_data="draft:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="仅发布一次", callback_data="draft:interval:once")],
            [
                InlineKeyboardButton(text="每 30 分钟", callback_data="draft:interval:30m"),
                InlineKeyboardButton(text="每 1 小时", callback_data="draft:interval:1h"),
            ],
            [
                InlineKeyboardButton(text="每 6 小时", callback_data="draft:interval:6h"),
                InlineKeyboardButton(text="每 24 小时", callback_data="draft:interval:24h"),
            ],
            [InlineKeyboardButton(text="❌ 取消", callback_data="draft:cancel")],
        ]
    )


def preview_keyboard(
    button_text: str | None,
    button_url: str | None,
    template_buttons: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if template_buttons:
        rows.extend([[InlineKeyboardButton(text=text, url=url)] for text, url in template_buttons])
    elif button_text and button_url:
        rows.append([InlineKeyboardButton(text=button_text, url=button_url)])
    rows.extend(
        [
            [InlineKeyboardButton(text="✅ 确认发布", callback_data="draft:publish")],
            [InlineKeyboardButton(text="✏️ 修改内容", callback_data="draft:modify")],
            [InlineKeyboardButton(text="❌ 取消发布", callback_data="draft:cancel")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draft_modify_menu(has_button: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 修改正文/图片", callback_data="draft:modify:content")
    builder.button(
        text="🔗 修改按钮" if has_button else "➕ 添加按钮",
        callback_data="draft:modify:button",
    )
    if has_button:
        builder.button(text="🗑 删除按钮", callback_data="draft:modify:remove_button")
    builder.button(text="📣 修改频道", callback_data="draft:modify:target")
    builder.button(text="👁 返回预览", callback_data="draft:modify:back")
    builder.adjust(1)
    return builder.as_markup()


def posts_keyboard(posts: list[Post], offset: int, page_size: int = 8) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for post in posts:
        status = STATUS_LABELS.get(post.status, post.status)
        excerpt = (post.text or "[图片]").replace("\n", " ")[:22]
        builder.button(text=f"#{post.id} · {status} · {excerpt}", callback_data=f"post:{post.id}")
    builder.adjust(1)
    nav: list[InlineKeyboardButton] = []
    if offset:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ 上一页", callback_data=f"posts:{max(0, offset - page_size)}"
            )
        )
    if len(posts) == page_size:
        nav.append(
            InlineKeyboardButton(text="下一页 ➡️", callback_data=f"posts:{offset + page_size}")
        )
    rows = builder.export()
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 主菜单", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def post_actions(
    post_id: int, deliveries: list[Delivery], schedules: list[ScheduledPush] | None = None
) -> InlineKeyboardMarkup:
    active = any(item.message_id and item.status != "deleted" for item in deliveries)
    rows: list[list[InlineKeyboardButton]] = []
    if active:
        rows.extend(
            [
                [InlineKeyboardButton(text="📝 修改正文", callback_data=f"manage:{post_id}:text")],
                [InlineKeyboardButton(text="🖼 更换图片", callback_data=f"manage:{post_id}:photo")],
                [
                    InlineKeyboardButton(
                        text="🔗 修改按钮", callback_data=f"manage:{post_id}:button"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚫 删除按钮", callback_data=f"manage:{post_id}:remove_button"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 删除频道消息", callback_data=f"manage:{post_id}:delete"
                    )
                ],
            ]
        )
    if schedules and any(item.active for item in schedules):
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏹ 停止定时推送", callback_data=f"schedule:stop:{post_id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ 返回列表", callback_data="posts:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scope_keyboard(
    post_id: int, operation: str, deliveries: list[Delivery]
) -> InlineKeyboardMarkup:
    active = [item for item in deliveries if item.message_id and item.status != "deleted"]
    rows = [
        [
            InlineKeyboardButton(
                text=item.channel_username,
                callback_data=f"scope:{post_id}:{operation}:{item.channel_key}",
            )
        ]
        for item in active
    ]
    if len(active) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="同步操作两个频道", callback_data=f"scope:{post_id}:{operation}:all"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="❌ 取消", callback_data=f"post:{post_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def link_keyboard(text: str | None, url: str | None) -> InlineKeyboardMarkup | None:
    if text and url:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])
    return None


def template_keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, url=url)] for text, url in buttons]
    )


def post_summary(
    post: Post, deliveries: list[Delivery], schedules: list[ScheduledPush] | None = None
) -> str:
    delivery_lines = []
    for item in deliveries:
        state = STATUS_LABELS.get(item.status, item.status)
        suffix = f"（{item.last_error}）" if item.last_error else ""
        delivery_lines.append(f"• {item.channel_username}: {state}{suffix}")
    if post.button_text == "__template__":
        button = "频道快捷模板"
    else:
        button = f"{post.button_text} → {post.button_url}" if post.button_text else "无"
    schedule_text = "未启用"
    active_schedules = [item for item in schedules or [] if item.active]
    if active_schedules:
        schedule_text = "；".join(
            f"{item.channel_key}: 每 {format_interval(item.interval_seconds)}，"
            f"下次 {item.next_run_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
            + (f"（上次失败：{item.last_error}）" if item.last_error else "")
            for item in active_schedules
        )
    return (
        f"发布记录 #{post.id}\n"
        f"状态：{STATUS_LABELS.get(post.status, post.status)}\n"
        f"创建时间：{post.created_at.astimezone().strftime('%Y-%m-%d %H:%M')}\n"
        f"正文：{post.text or '（无正文）'}\n"
        f"图片：{'有' if post.photo_file_id else '无'}\n"
        f"按钮：{button}\n"
        f"定时推送：{schedule_text}\n\n" + "\n".join(delivery_lines)
    )


def format_interval(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} 天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 60} 分钟"
