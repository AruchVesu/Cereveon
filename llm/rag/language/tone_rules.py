TONE_RULES = {
    "decisive": {
        "style": "urgent and definitive",
        "forbidden": ["maybe", "might", "unclear", "balanced"],
        "required": ["forced", "inevitable", "decisive"],
    },
    "high": {
        "style": "confident and clear",
        "forbidden": ["maybe", "unclear"],
        "required": [],
    },
    "moderate": {
        "style": "calm and explanatory",
        "forbidden": [],
        "required": [],
    },
    "neutral": {
        "style": "balanced and careful",
        "forbidden": ["winning", "decisive", "crushing"],
        "required": ["balanced", "no clear advantage"],
    },
    "uncertain": {
        "style": "cautious and probabilistic",
        "forbidden": ["definitely", "clearly winning"],
        "required": ["uncertain", "unclear"],
    },
}
