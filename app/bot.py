import asyncio
import html
import logging
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import db, rag
from app.config import settings

log = logging.getLogger(__name__)
router = Router()

CONTENT_TYPE_LABELS = {
    "article": "статья",
    "lesson": "урок",
    "course": "курс",
    "document": "шаблон",
    "glossary": "глоссарий",
    "webinar": "вебинар",
    "podcast": "подкаст",
}

# ---------- Анкета о бизнесе ----------

PROFILE_QUESTIONS = [
    ("stage", "На какой стадии ваш бизнес?", [
        ("idea", "Только идея"), ("launch", "Запускаюсь"),
        ("running", "Уже работаю"), ("growth", "Масштабируюсь"),
    ]),
    ("legal_form", "В какой форме вы работаете?", [
        ("ip", "ИП"), ("ooo", "ООО"), ("self_employed", "Самозанятый"), ("none", "Пока не оформлен"),
    ]),
    ("tax_regime", "Какой у вас налоговый режим?", [
        ("usn6", "УСН 6%"), ("usn15", "УСН 15%"), ("patent", "Патент"),
        ("npd", "НПД"), ("osno", "ОСНО"), ("unknown", "Не знаю"),
    ]),
    ("experience", "Какой у вас опыт в предпринимательстве?", [
        ("beginner", "Новичок"), ("intermediate", "1–3 года"), ("advanced", "Больше 3 лет"),
    ]),
]


OPTION_LABELS = {(field, value): label for field, _, opts in PROFILE_QUESTIONS for value, label in opts}


def _profile_summary(profile: dict | None) -> str:
    if not profile:
        return "пока ничего не заполнено"
    lines = []
    names = {"stage": "Стадия", "legal_form": "Форма", "tax_regime": "Налоговый режим",
             "experience": "Опыт", "industry": "Сфера"}
    for field, name in names.items():
        value = profile.get(field)
        if value:
            lines.append(f"• {name}: {html.escape(OPTION_LABELS.get((field, value), value))}")
    return "\n".join(lines) or "пока ничего не заполнено"


class ProfileForm(StatesGroup):
    step = State()
    industry = State()


def _keyboard(field: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for value, label in options:
        row.append(InlineKeyboardButton(text=label, callback_data=f"pf:{field}:{value}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Пропустить", callback_data=f"pf:{field}:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_step(target: Message, state: FSMContext, idx: int) -> None:
    if idx >= len(PROFILE_QUESTIONS):
        await state.set_state(ProfileForm.industry)
        await target.answer("И последнее: в какой сфере ваш бизнес? Напишите в двух словах (или /skip).")
        return
    field, question, options = PROFILE_QUESTIONS[idx]
    await state.set_state(ProfileForm.step)
    await state.update_data(idx=idx)
    await target.answer(question, reply_markup=_keyboard(field, options))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await db.touch_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await db.log_event(message.from_user.id, "start")
    await state.clear()
    await message.answer(
        "Здравствуйте! Я помощник «Альфа-Курса» для предпринимателей.\n\n"
        "Задайте вопрос о бизнесе: налоги, учёт, маркетинг, продажи. Я отвечу по материалам "
        "платформы и покажу, откуда взят ответ.\n\n"
        "Чтобы ответы были точнее, можно пройти короткую анкету: /profile (4 вопроса, любой можно пропустить)."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "/profile — анкета о бизнесе\n"
        "/me — что я знаю о вашем бизнесе\n"
        "/courses — подобрать курсы под ваш бизнес\n"
        "Просто напишите вопрос, чтобы получить ответ по базе знаний."
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext) -> None:
    await db.touch_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await _ask_step(message, state, 0)


@router.callback_query(ProfileForm.step, F.data.startswith("pf:"))
async def on_profile_answer(cb: CallbackQuery, state: FSMContext) -> None:
    _, field, value = cb.data.split(":", 2)
    if value != "skip":
        await db.upsert_profile(cb.from_user.id, **{field: value})
        await db.log_event(cb.from_user.id, "profile_update", {field: value})
    # Показываем выбранный вариант прямо в сообщении с вопросом, чтобы в чате был виден ответ
    choice = "пропущено" if value == "skip" else OPTION_LABELS.get((field, value), value)
    question = html.escape(cb.message.text or "")
    await cb.message.edit_text(f"{question}\n<b>→ {html.escape(choice)}</b>", reply_markup=None)
    await cb.answer(choice)
    idx = (await state.get_data()).get("idx", 0) + 1
    await _ask_step(cb.message, state, idx)


@router.message(ProfileForm.industry)
async def on_industry(message: Message, state: FSMContext) -> None:
    if message.text and message.text != "/skip":
        await db.upsert_profile(message.from_user.id, industry=message.text.strip()[:200])
    await state.clear()
    profile = await db.get_profile(message.from_user.id)
    await message.answer(
        f"Спасибо! Вот что я запомнил о вашем бизнесе:\n{_profile_summary(profile)}\n\n"
        "Учту это в ответах. Изменить можно командой /profile. Задавайте вопрос!"
    )


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    profile = await db.get_profile(message.from_user.id)
    await message.answer(f"Что я знаю о вашем бизнесе:\n{_profile_summary(profile)}\n\nИзменить: /profile")


# ---------- Вопросы к базе знаний ----------

def _render_sources(sources: list[dict]) -> str:
    lines = []
    for s in sources:
        kind = CONTENT_TYPE_LABELS.get(s["content_type"], s["content_type"])
        paid = ", платный" if s["is_paid"] else ""
        title = html.escape(s["title"])
        if s.get("url"):
            title = f'<a href="{html.escape(s["url"], quote=True)}">{title}</a>'
        lines.append(f"[{s['n']}] {title} ({kind}{paid})")
    return "\n".join(lines)


@router.message(F.text & ~F.text.startswith("/"))
async def on_question(message: Message) -> None:
    user = message.from_user
    await db.touch_user(user.id, user.username, user.first_name)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    question = message.text.strip()
    profile = await db.get_profile(user.id)
    if profile:  # в LLM передаём понятные подписи, а не коды (usn6 -> «УСН 6%»)
        profile = {k: OPTION_LABELS.get((k, v), v) for k, v in profile.items()}
    history = await db.recent_dialog(user.id, limit=4)
    await db.save_message(user.id, "user", question)
    await db.log_event(user.id, "question", {"text": question})

    try:
        # Если прошлым сообщением бот уточнял вопрос, ищем по исходному вопросу вместе с уточнением
        search_query = question
        if len(history) >= 2 and history[-1]["role"] == "assistant" and history[-1]["answer_type"] == "clarify" \
                and history[-2]["role"] == "user":
            search_query = f"{history[-2]['text']} {question}"
        result = await rag.answer(question, profile=profile, history=history, search_query=search_query)
    except Exception:
        log.exception("RAG failed")
        await message.answer("Не получилось обработать вопрос, попробуйте ещё раз чуть позже.")
        return

    text = _render_answer(result)
    await db.save_message(user.id, "assistant", result.text, result.kind, result.sources)
    await db.log_event(user.id, "answer_shown", {
        "kind": result.kind,
        "doc_ids": [s["document_id"] for s in result.sources],
        "recommended_ids": [r["document_id"] for r in result.recommendations],
    })
    await message.answer(text, disable_web_page_preview=True)


def _link(title: str, url: str | None) -> str:
    title = html.escape(title)
    return f'<a href="{html.escape(url, quote=True)}">{title}</a>' if url else title


def _render_courses(items: list[dict]) -> str:
    lines = []
    for i, c in enumerate(items, 1):
        price = c["price"] or ("платный" if c["is_paid"] else "бесплатный")
        price = price.rstrip(".")
        price = price[:1].lower() + price[1:]
        line = f"{i}. {_link(c['title'], c['url'])} ({html.escape(price)})"
        if c["summary"]:
            line += f"\n   {html.escape(c['summary'])}"
        lines.append(line)
    return "\n".join(lines)


def _render_answer(result: rag.Answer) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(result.text))
    if result.kind == "answer" and result.sources:
        text += "\n\n<b>Источники:</b>\n" + _render_sources(result.sources)
        if result.recommendations:
            text += "\n\n<b>Чтобы разобраться глубже:</b>\n" + _render_courses(result.recommendations)
    elif result.kind == "courses":
        text += "\n\n" + _render_courses(result.recommendations)
        text += "\n\nМогу рассказать подробнее о любом из них или подобрать материалы под конкретную задачу."
    elif result.kind == "not_found":
        if result.sources:
            text += "\n\nВозможно, пригодятся близкие материалы:\n" + _render_sources(result.sources)
        text += "\n\nПопробуйте переформулировать вопрос или посмотрите курсы: /courses"
    return text


@router.message(Command("courses"))
async def cmd_courses(message: Message) -> None:
    user = message.from_user
    await db.touch_user(user.id, user.username, user.first_name)
    profile = await db.get_profile(user.id)
    if profile:
        profile = {k: OPTION_LABELS.get((k, v), v) for k, v in profile.items()}
    query = "курсы для предпринимателя " + rag._format_profile(profile) if profile else "курсы для начинающего предпринимателя"
    result = await rag.courses(query, profile)
    await db.log_event(user.id, "courses_shown", {"ids": [r["document_id"] for r in result.recommendations]})
    await message.answer(_render_answer(result), disable_web_page_preview=True)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен бота.")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await db.get_pool()
    try:
        await dp.start_polling(bot)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
