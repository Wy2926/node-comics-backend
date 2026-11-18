"""Validate model output before it becomes reader-visible text."""
import json
import re


def _unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError()
    return result


def translation_json(content):
    return json.loads(content, object_pairs_hook=_unique_keys)


MODEL_MARKUP = re.compile(r'```|</?(?:think|analysis|reasoning)>', re.I)
LABELED_PAIR = re.compile(r'^(?:original|source|原文)[ \t]*[:：](.*?)\n[ \t]*(?:translation|译文|譯文)[ \t]*[:：]', re.I | re.S)
EDITORIAL_LABEL = re.compile(r'^(?:translation|translated text|译文|譯文|翻译|翻譯)[ \t]*[:：]|'
                             r"(?:^|\n[ \t]*|[(（\[【])(?:translator['’]s note|T/N|译者?注|譯者?[註注])[ \t]*[:：]", re.I)


def printed_text(value, source):
    if not isinstance(value, str) or re.search(r'[\x00\ud800-\udfff]', value):
        raise ValueError()
    value = value.strip()
    if source is not None:
        # Literal markup and multilingual dialogue can themselves be part of a comic.
        literal = source.lower()
        if any(mark.group().lower() not in literal for mark in MODEL_MARKUP.finditer(value)):
            raise ValueError()
        if EDITORIAL_LABEL.search(value) and not any(c in source for c in '\r\n:：([{（【'):
            raise ValueError()
        pair = LABELED_PAIR.match(value)
        if pair and pair.group(1).strip() == source.strip():
            raise ValueError()
    return value
