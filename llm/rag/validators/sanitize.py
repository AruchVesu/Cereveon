import re

NOTATION_REGEX = re.compile(r"\b[BNRQK]?[a-h][1-8](x[a-h][1-8])?(=[BNRQ])?\+?\b")


def mask_chess_notation(text: str) -> str:
    return NOTATION_REGEX.sub("[REDACTED]", text)
