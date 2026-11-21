import re


def normalise(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def helper_token():
    return normalise("x")
