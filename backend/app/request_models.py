"""Shared validation policy for JSON request bodies; response models stay independent."""
from pydantic import BaseModel, ConfigDict, model_validator


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranslationBody(RequestBody):
    @model_validator(mode='before')
    @classmethod
    def ignore_obsolete_title(cls, data):
        if isinstance(data, dict) and 'comic_title' in data:
            return {key: value for key, value in data.items() if key != 'comic_title'}
        return data
