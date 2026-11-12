"""隐私保护：报告与评论中出现的手机号 / 邮箱 / 身份账号一律打码，不做二次传播。

只做「就地打码」，不整段删除；当事人公开的社交账号（@handle / 主页链接）保留原样，
因为那是溯源所需。
"""

from __future__ import annotations

import re

_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)(0\d{2,3}[-— ]?\d{7,8})(?!\d)")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*(@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_ID_CARD = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)")


def _mask_phone(match: re.Match[str]) -> str:
    value = match.group(1)
    return f"{value[:3]}****{value[-4:]}"


def _mask_landline(match: re.Match[str]) -> str:
    value = match.group(1)
    return f"{value[:4]}****{value[-2:]}"


def _mask_email(match: re.Match[str]) -> str:
    return f"{match.group(1)}***{match.group(2)}"


def _mask_id(match: re.Match[str]) -> str:
    return f"{match.group(1)}********{match.group(3)[-1]}"


def mask_personal(text: str) -> str:
    """打码手机号、座机、邮箱、身份证号；空值原样返回。"""
    if not text:
        return text
    masked = _PHONE.sub(_mask_phone, text)
    masked = _LANDLINE.sub(_mask_landline, masked)
    masked = _EMAIL.sub(_mask_email, masked)
    masked = _ID_CARD.sub(_mask_id, masked)
    return masked
