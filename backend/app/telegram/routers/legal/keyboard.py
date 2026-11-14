from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fluentogram import TranslatorRunner

from app.core.constants import CUSTOMER_NOTIFICATION_CLOSE_CALLBACK
from app.modules.legal import LegalRevisionDto
from app.telegram.legal_gate import LegalDocumentCallback


def legal_keyboard(
    runner: TranslatorRunner, documents: list[LegalRevisionDto]
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=runner.get(f"btn-legal.{document.kind}"),
                    callback_data=LegalDocumentCallback(kind=document.kind).pack(),
                )
            ]
            for document in documents
        ]
        + close_keyboard(runner).inline_keyboard
    )


def close_keyboard(runner: TranslatorRunner) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=runner.get("ntf-customer-button-close"),
                    callback_data=CUSTOMER_NOTIFICATION_CLOSE_CALLBACK,
                )
            ]
        ]
    )
