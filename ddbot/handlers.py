from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .config import Settings
from .db import Database
from .models import Delivery, Post
from .scheduler import PushScheduler
from .service import PublishingService, format_results
from .states import DraftFlow, ManageFlow
from .ui import (
    channel_choice,
    draft_modify_menu,
    format_interval,
    main_menu,
    post_actions,
    post_summary,
    posts_keyboard,
    preview_keyboard,
    schedule_choice,
    scope_keyboard,
    valid_button_url,
)

router = Router(name=__name__)
logger = logging.getLogger(__name__)


@router.channel_post()
async def relay_channel_post(message: Message, settings: Settings, db: Database) -> None:
    await relay_to_topic(message, settings, db, replace=False)


@router.edited_channel_post()
async def relay_edited_channel_post(message: Message, settings: Settings, db: Database) -> None:
    await relay_to_topic(message, settings, db, replace=True)


async def relay_to_topic(message: Message, settings: Settings, db: Database, replace: bool) -> None:
    username = (message.chat.username or "").lower()
    channel_key = next(
        (
            key
            for key, channel in settings.channels.items()
            if username == channel.removeprefix("@").lower()
        ),
        None,
    )
    if not channel_key:
        return
    topic_id = settings.topics[channel_key]
    previous = await db.get_relay(message.chat.id, message.message_id)
    if not replace:
        claimed = await db.claim_relay(
            message.chat.id,
            message.message_id,
            channel_key,
            settings.target_group_id,
            topic_id,
        )
        if not claimed:
            return
    elif not previous:
        await db.claim_relay(
            message.chat.id,
            message.message_id,
            channel_key,
            settings.target_group_id,
            topic_id,
        )
    try:
        forwarded = await message.bot.forward_message(
            chat_id=settings.target_group_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=topic_id,
        )
        await db.finish_relay(
            message.chat.id, message.message_id, "forwarded", forwarded.message_id
        )
        if replace and previous and previous.get("forwarded_message_id"):
            try:
                await message.bot.delete_message(
                    settings.target_group_id, previous["forwarded_message_id"]
                )
            except TelegramAPIError:
                logger.exception(
                    "Edited post re-forwarded, but old topic copy could not be deleted: %s",
                    previous["forwarded_message_id"],
                )
    except TelegramAPIError as exc:
        error = str(exc)[:300]
        await db.finish_relay(message.chat.id, message.message_id, "failed", error=error)
        logger.exception(
            "Failed to relay channel post %s/%s to topic %s",
            message.chat.id,
            message.message_id,
            topic_id,
        )


def deadline(settings: Settings) -> str:
    return (datetime.now(UTC) + timedelta(minutes=settings.flow_timeout_minutes)).isoformat()


async def ensure_active(event: Message | CallbackQuery, state: FSMContext) -> bool:
    data = await state.get_data()
    expiry = data.get("expires_at")
    if not expiry or datetime.fromisoformat(expiry) >= datetime.now(UTC):
        return True
    await state.clear()
    text = "当前操作已超时，请重新开始。"
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text, reply_markup=main_menu())
    return False


async def start_draft(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await state.set_state(DraftFlow.content)
    await state.update_data(expires_at=deadline(settings))
    await message.answer(
        "请发送正文，或上传一张图片并在图片说明中填写正文。\n"
        "纯图片也可以发布；发送 /cancel 可随时取消。"
    )


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("频道内容管理", reply_markup=main_menu())


@router.message(Command("new"))
async def command_new(message: Message, state: FSMContext, settings: Settings) -> None:
    await start_draft(message, state, settings)


@router.callback_query(F.data == "new")
async def callback_new(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await query.answer()
    if query.message:
        await start_draft(query.message, state, settings)


@router.message(Command("cancel"))
async def command_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("当前操作已取消。", reply_markup=main_menu())


@router.callback_query(F.data == "draft:cancel")
async def callback_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer("已取消")
    if query.message:
        await query.message.answer("发布已取消。", reply_markup=main_menu())


@router.message(DraftFlow.content)
async def receive_content(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_active(message, state):
        return
    if not message.text and not message.photo:
        await message.answer("请发送文字或一张图片。")
        return
    text = message.caption if message.photo else message.text
    photo = message.photo[-1].file_id if message.photo else None
    await state.update_data(
        text=text, photo_file_id=photo, button_text="__template__", button_url=None
    )
    await state.set_state(DraftFlow.target)
    await message.answer(
        "已自动套用对应语言的三行按钮模板。请选择目标频道：",
        reply_markup=channel_choice(settings.channels),
    )


@router.callback_query(F.data == "draft:button:add")
async def add_button(query: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_active(query, state):
        return
    await query.answer()
    await state.set_state(DraftFlow.button_text)
    if query.message:
        await query.message.answer("请输入按钮显示文字（1–64 个字符）。")


@router.callback_query(F.data == "draft:button:skip")
async def skip_button(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await ensure_active(query, state):
        return
    await query.answer()
    await state.update_data(button_text=None, button_url=None)
    await state.set_state(DraftFlow.target)
    if query.message:
        await query.message.answer(
            "请选择目标频道：", reply_markup=channel_choice(settings.channels)
        )


@router.callback_query(F.data == "draft:button:template")
async def use_button_template(
    query: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    if not await ensure_active(query, state):
        return
    await query.answer()
    await state.update_data(button_text="__template__", button_url=None)
    await state.set_state(DraftFlow.target)
    if query.message:
        await query.message.answer(
            "请选择目标频道：", reply_markup=channel_choice(settings.channels)
        )


@router.message(DraftFlow.button_text)
async def receive_button_text(message: Message, state: FSMContext) -> None:
    if not await ensure_active(message, state):
        return
    value = (message.text or "").strip()
    if not 1 <= len(value) <= 64:
        await message.answer("按钮文字必须为 1–64 个字符，请重新输入。")
        return
    await state.update_data(button_text=value)
    await state.set_state(DraftFlow.button_url)
    await message.answer("请输入按钮链接（支持 http://、https:// 或 tg://）。")


@router.message(DraftFlow.button_url)
async def receive_button_url(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    if not await ensure_active(message, state):
        return
    value = (message.text or "").strip()
    if not valid_button_url(value):
        await message.answer("链接格式不正确，请输入完整的 http(s):// 或 tg:// 链接。")
        return
    await state.update_data(button_url=value)
    await state.set_state(DraftFlow.target)
    await message.answer("请选择目标频道：", reply_markup=channel_choice(settings.channels))


@router.callback_query(F.data.startswith("draft:target:"))
async def choose_target(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await ensure_active(query, state):
        return
    selection = (query.data or "").rsplit(":", 1)[-1]
    keys = list(settings.channels) if selection in {"all", "both"} else [selection]
    if any(key not in settings.channels for key in keys):
        await query.answer("无效频道", show_alert=True)
        return
    await state.update_data(target_keys=keys)
    await state.set_state(DraftFlow.interval)
    await query.answer()
    if query.message:
        await query.message.answer(
            "请选择定时推送间隔。首次会立即发布；选择“仅发布一次”则不重复推送。",
            reply_markup=schedule_choice(),
        )


@router.callback_query(F.data.startswith("draft:interval:"))
async def choose_interval(
    query: CallbackQuery, state: FSMContext, settings: Settings
) -> None:
    if not await ensure_active(query, state):
        return
    choice = (query.data or "").rsplit(":", 1)[-1]
    values = {"once": None, "30m": 1800, "1h": 3600, "6h": 21600, "24h": 86400}
    if choice not in values:
        await query.answer("无效间隔", show_alert=True)
        return
    await state.update_data(interval_seconds=values[choice])
    await state.set_state(DraftFlow.preview)
    await query.answer()
    if query.message:
        await show_preview(query.message, state, settings)


async def show_preview(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    targets = "、".join(settings.channels[key] for key in data["target_keys"])
    label = f"发布预览\n目标频道：{targets}"
    interval_seconds = data.get("interval_seconds")
    label += (
        f"\n定时推送：每 {format_interval(interval_seconds)}"
        if interval_seconds
        else "\n定时推送：仅发布一次"
    )
    template = None
    if data.get("button_text") == "__template__":
        # 各频道模板文案不同；预览展示第一个目标频道的版本。
        template = settings.template_buttons(data["target_keys"][0])
        label += f"\n按钮模板：{settings.channels[data['target_keys'][0]]} 版本"
    markup = preview_keyboard(data.get("button_text"), data.get("button_url"), template)
    if data.get("photo_file_id"):
        await message.answer_photo(
            data["photo_file_id"], caption=data.get("text"), reply_markup=markup
        )
        await message.answer(label)
    else:
        await message.answer(f"{data.get('text') or ''}\n\n—\n{label}", reply_markup=markup)


@router.callback_query(F.data == "draft:publish")
async def publish_draft(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    service: PublishingService,
    scheduler: PushScheduler,
) -> None:
    if not await ensure_active(query, state):
        return
    data = await state.get_data()
    if not data.get("target_keys"):
        await query.answer("发布数据不完整，请重新创建", show_alert=True)
        return
    await query.answer("正在发布…")
    targets = {key: settings.channels[key] for key in data["target_keys"]}
    post_id, results = await service.publish(
        query.from_user.id,
        data.get("text"),
        data.get("photo_file_id"),
        data.get("button_text"),
        data.get("button_url"),
        targets,
    )
    await scheduler.schedule(post_id, data["target_keys"], data.get("interval_seconds"))
    await state.clear()
    if query.message:
        await query.message.answer(
            f"发布记录 #{post_id}\n{format_results(results)}", reply_markup=main_menu()
        )


@router.callback_query(F.data == "draft:modify")
async def modify_draft(query: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_active(query, state):
        return
    data = await state.get_data()
    await query.answer()
    if query.message:
        await query.message.answer(
            "请选择要修改的项目：", reply_markup=draft_modify_menu(bool(data.get("button_text")))
        )


@router.callback_query(F.data.startswith("draft:modify:"))
async def modify_draft_item(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await ensure_active(query, state):
        return
    action = (query.data or "").rsplit(":", 1)[-1]
    await query.answer()
    if action == "content":
        await state.set_state(DraftFlow.modify_content)
        text = "请重新发送正文或图片（新内容将完整替换当前正文/图片）。"
    elif action == "button":
        await state.set_state(DraftFlow.modify_button_text)
        text = "请输入新的按钮显示文字。"
    elif action == "remove_button":
        await state.update_data(button_text=None, button_url=None)
        text = "按钮已删除。"
        if query.message:
            await show_preview(query.message, state, settings)
        return
    elif action == "target":
        await state.set_state(DraftFlow.target)
        if query.message:
            await query.message.answer(
                "请选择目标频道：", reply_markup=channel_choice(settings.channels)
            )
        return
    else:
        if query.message:
            await show_preview(query.message, state, settings)
        return
    if query.message:
        await query.message.answer(text)


@router.message(DraftFlow.modify_content)
async def receive_modified_content(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_active(message, state):
        return
    if not message.text and not message.photo:
        await message.answer("请发送文字或图片。")
        return
    await state.update_data(
        text=message.caption if message.photo else message.text,
        photo_file_id=message.photo[-1].file_id if message.photo else None,
    )
    await state.set_state(DraftFlow.preview)
    await show_preview(message, state, settings)


@router.message(DraftFlow.modify_button_text)
async def modified_button_text(message: Message, state: FSMContext) -> None:
    if not await ensure_active(message, state):
        return
    value = (message.text or "").strip()
    if not 1 <= len(value) <= 64:
        await message.answer("按钮文字必须为 1–64 个字符。")
        return
    await state.update_data(button_text=value)
    await state.set_state(DraftFlow.modify_button_url)
    await message.answer("请输入新的按钮链接。")


@router.message(DraftFlow.modify_button_url)
async def modified_button_url(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await ensure_active(message, state):
        return
    value = (message.text or "").strip()
    if not valid_button_url(value):
        await message.answer("链接格式不正确，请重新输入。")
        return
    await state.update_data(button_url=value)
    await state.set_state(DraftFlow.preview)
    await show_preview(message, state, settings)


@router.message(Command("posts"))
async def command_posts(message: Message, db: Database) -> None:
    posts = await db.recent_posts(8)
    await message.answer(
        "最近发布记录：" if posts else "暂无发布记录。", reply_markup=posts_keyboard(posts, 0)
    )


@router.callback_query(F.data.startswith("posts:"))
async def list_posts(query: CallbackQuery, db: Database) -> None:
    offset = max(0, int((query.data or "posts:0").split(":")[1]))
    posts = await db.recent_posts(8, offset)
    await query.answer()
    if query.message:
        await query.message.answer(
            "发布记录：" if posts else "本页没有记录。", reply_markup=posts_keyboard(posts, offset)
        )


@router.callback_query(F.data.startswith("post:"))
async def view_post(query: CallbackQuery, db: Database) -> None:
    post_id = int((query.data or "").split(":")[1])
    post = await db.get_post(post_id)
    if not post:
        await query.answer("记录不存在", show_alert=True)
        return
    deliveries = await db.get_deliveries(post_id)
    schedules = await db.get_scheduled_pushes(post_id=post_id)
    await query.answer()
    if query.message:
        await query.message.answer(
            post_summary(post, deliveries, schedules),
            reply_markup=post_actions(post_id, deliveries, schedules),
        )


@router.callback_query(F.data.startswith("schedule:stop:"))
async def stop_schedule(query: CallbackQuery, scheduler: PushScheduler) -> None:
    post_id = int((query.data or "").rsplit(":", 1)[-1])
    stopped = await scheduler.stop(post_id)
    await query.answer("定时推送已停止" if stopped else "该任务已经停止")
    if query.message:
        await query.message.answer(
            "已停止后续定时推送；此前发布的消息不会被删除。",
            reply_markup=main_menu(),
        )


@router.callback_query(F.data.startswith("manage:"))
async def choose_manage_scope(query: CallbackQuery, db: Database) -> None:
    _, raw_id, operation = (query.data or "").split(":")
    post_id = int(raw_id)
    deliveries = await db.get_deliveries(post_id)
    await query.answer()
    if query.message:
        await query.message.answer(
            "请选择要操作的频道：", reply_markup=scope_keyboard(post_id, operation, deliveries)
        )


def select_deliveries(deliveries: list[Delivery], scope: str) -> list[Delivery]:
    return deliveries if scope == "all" else [d for d in deliveries if d.channel_key == scope]


async def update_selected_content(
    db: Database, deliveries: list[Delivery], **fields: str | None
) -> list[Delivery]:
    for delivery in deliveries:
        await db.update_delivery_content(delivery.id, **fields)
    if not deliveries:
        return []
    selected_ids = {delivery.id for delivery in deliveries}
    return [
        item for item in await db.get_deliveries(deliveries[0].post_id) if item.id in selected_ids
    ]


@router.callback_query(F.data.startswith("scope:"))
async def apply_scope(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    service: PublishingService,
    settings: Settings,
) -> None:
    _, raw_id, operation, scope = (query.data or "").split(":")
    post_id = int(raw_id)
    post = await db.get_post(post_id)
    if not post:
        await query.answer("记录不存在", show_alert=True)
        return
    deliveries = select_deliveries(await db.get_deliveries(post_id), scope)
    await query.answer()
    if operation == "delete":
        results = await service.delete(post_id, deliveries)
        if query.message:
            await query.message.answer(format_results(results), reply_markup=main_menu())
        return
    if operation == "remove_button":
        await db.update_post_content(post_id, button_text=None, button_url=None)
        deliveries = await update_selected_content(
            db, deliveries, button_text=None, button_url=None
        )
        post = await db.get_post(post_id)
        assert post
        results = await service.edit(post, deliveries)
        if query.message:
            await query.message.answer(format_results(results), reply_markup=main_menu())
        return
    state_map = {
        "text": ManageFlow.text,
        "photo": ManageFlow.photo,
        "button": ManageFlow.button_text,
    }
    await state.set_state(state_map[operation])
    await state.update_data(
        post_id=post_id, scope=scope, operation=operation, expires_at=deadline(settings)
    )
    prompts = {
        "text": "请输入新的正文。",
        "photo": "请上传新的图片。",
        "button": "请输入新的按钮显示文字。",
    }
    if query.message:
        await query.message.answer(prompts[operation])


async def load_management(
    state: FSMContext, db: Database
) -> tuple[dict, Post, list[Delivery]] | None:
    data = await state.get_data()
    post = await db.get_post(data["post_id"])
    if not post:
        return None
    deliveries = select_deliveries(await db.get_deliveries(post.id), data["scope"])
    return data, post, deliveries


@router.message(ManageFlow.text)
async def manage_text(
    message: Message, state: FSMContext, db: Database, service: PublishingService
) -> None:
    if not await ensure_active(message, state):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("正文不能为空，请重新输入。")
        return
    loaded = await load_management(state, db)
    if not loaded:
        await state.clear()
        return
    _, post, deliveries = loaded
    await db.update_post_content(post.id, text=text)
    deliveries = await update_selected_content(db, deliveries, text=text)
    updated = await db.get_post(post.id)
    assert updated
    results = await service.edit(updated, deliveries)
    await state.clear()
    await message.answer(format_results(results), reply_markup=main_menu())


@router.message(ManageFlow.photo)
async def manage_photo(
    message: Message, state: FSMContext, db: Database, service: PublishingService
) -> None:
    if not await ensure_active(message, state):
        return
    if not message.photo:
        await message.answer("请上传一张图片。")
        return
    loaded = await load_management(state, db)
    if not loaded:
        await state.clear()
        return
    _, post, deliveries = loaded
    old_media_ids = {delivery.id for delivery in deliveries if delivery.photo_file_id}
    await db.update_post_content(post.id, photo_file_id=message.photo[-1].file_id)
    deliveries = await update_selected_content(
        db, deliveries, photo_file_id=message.photo[-1].file_id
    )
    updated = await db.get_post(post.id)
    assert updated
    media_deliveries = [item for item in deliveries if item.id in old_media_ids]
    text_deliveries = [item for item in deliveries if item.id not in old_media_ids]
    results = await service.edit(updated, media_deliveries)
    results.extend(await service.replace_text_with_photo(updated, text_deliveries))
    await state.clear()
    await message.answer(format_results(results), reply_markup=main_menu())


@router.message(ManageFlow.button_text)
async def manage_button_text(message: Message, state: FSMContext) -> None:
    if not await ensure_active(message, state):
        return
    value = (message.text or "").strip()
    if not 1 <= len(value) <= 64:
        await message.answer("按钮文字必须为 1–64 个字符。")
        return
    await state.update_data(button_text=value)
    await state.set_state(ManageFlow.button_url)
    await message.answer("请输入新的按钮链接。")


@router.message(ManageFlow.button_url)
async def manage_button_url(
    message: Message, state: FSMContext, db: Database, service: PublishingService
) -> None:
    if not await ensure_active(message, state):
        return
    url = (message.text or "").strip()
    if not valid_button_url(url):
        await message.answer("链接格式不正确，请重新输入。")
        return
    loaded = await load_management(state, db)
    if not loaded:
        await state.clear()
        return
    data, post, deliveries = loaded
    await db.update_post_content(post.id, button_text=data["button_text"], button_url=url)
    deliveries = await update_selected_content(
        db, deliveries, button_text=data["button_text"], button_url=url
    )
    updated = await db.get_post(post.id)
    assert updated
    results = await service.edit(updated, deliveries)
    await state.clear()
    await message.answer(format_results(results), reply_markup=main_menu())


@router.callback_query(F.data == "channels")
async def show_channels(query: CallbackQuery, settings: Settings) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(
            "当前可发布频道：\n" + "\n".join(f"• {item}" for item in settings.channels.values()),
            reply_markup=main_menu(),
        )


@router.callback_query(F.data == "menu")
async def callback_menu(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.answer("频道内容管理", reply_markup=main_menu())
