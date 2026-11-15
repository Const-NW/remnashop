from aiogram.types import InlineKeyboardMarkup
from fluentogram import TranslatorRunner

from app.core.constants import CUSTOMER_NOTIFICATION_CLOSE_CALLBACK
from app.modules.legal import LegalRevisionDto
from app.telegram.dialogs.rendering import label_with_icon
from app.telegram.legal_gate import LegalDocumentCallback


def legal_keyboard(
    runner: TranslatorRunner, documents: list[LegalRevisionDto]
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                label_with_icon(runner, f"btn-legal.{document.kind}").inline_button(
                    callback_data=LegalDocumentCallback(kind=document.kind).pack()
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
                label_with_icon(runner, "btn-customer-notification.close").inline_button(
                    callback_data=CUSTOMER_NOTIFICATION_CLOSE_CALLBACK
                )
            ]
        ]
    )
