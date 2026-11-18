"""Body translation uses OCR and frozen guidance, without work-title metadata."""
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import classic
from app.adapters import text as page_text
from app.adapters.llm import TextResponse
from app.db import session_factory
from app.documents.api import DocumentInput
from app.models import ClassicState, Job, TextCall
from app.providers import digest
from app.translation_api import TranslationInput
from conftest import configure_system_limits, login, request_id, request_record
from test_classic import analysis, text_case, text_database  # noqa: F401
from test_cluster_submissions import cluster, descriptor, submit  # noqa: F401
from test_document_quota_api import body as document_body, create as create_document, document_case  # noqa: F401
from test_translation_prompts import persisted_job
from test_website_guests import visitor, submit as guest_submit  # noqa: F401


@pytest.mark.parametrize('kind', ['page', 'document'])
def test_obsolete_title_is_ignored_but_other_extra_fields_are_invalid(kind):
    model, data = (TranslationInput, dict(image=descriptor(b'page'), mode='classic', target_language='zh-Hans')) if kind == 'page' else (
        DocumentInput, dict(filename='book.zip', source_type='zip', byte_size=1234, sha256='a' * 64, target_language='zh-Hans'))
    assert model.model_validate({**data, 'comic_title': 'A work name'}).model_dump() == model.model_validate(data).model_dump()
    assert 'comic_title' not in model.model_json_schema()['properties']
    with pytest.raises(ValidationError) as error:
        model.model_validate({**data, 'comic_title': None, 'unknown': 'x'})
    assert error.value.errors()[0]['type'] == 'extra_forbidden'


@pytest.mark.parametrize('kind', ['page', 'document'])
def test_removed_title_http_response_does_not_echo_private_input(document_case, kind):
    case = document_case
    if kind == 'page':
        path = '/v1/translations/' + request_id('removed-title')
        body = {'image': descriptor(b'page'), 'mode': 'classic', 'target_language': 'zh-Hans'}
    else:
        path, body = '/v1/documents/' + request_id('removed-title'), document_body(case)
    response = case.client.put(path, headers=case.auth, json={**body, 'comic_title': 'PRIVATE_TITLE_MARKER', 'unknown': 'x'})
    assert response.status_code == 422
    assert 'PRIVATE_TITLE_MARKER' not in response.text and 'input' not in response.text


def test_page_request_keeps_existing_hash_and_one_job_without_title(client):
    auth, image = login(client), descriptor(b'no-title')
    expected = digest({'image': {**image, 'normalization_version': 1}, 'mode': 'classic',
        'target_language': 'zh-Hans', 'retry_of': None, 'regenerate_of': None, 'acknowledge_unknown_cost': False})
    response = submit(client, auth, image, key='no-title', comic_title='PRIVATE_TITLE_MARKER')
    original = persisted_job(client, auth, response)
    repeated = persisted_job(client, auth, submit(client, auth, image, key='no-title'))
    reused = persisted_job(client, auth, submit(client, auth, image, key='another-no-title', comic_title='Changed title'))
    assert repeated.id == reused.id == original.id and 'comic_title' not in original.config
    record = request_record(client, auth, response.json()['id'])
    assert 'PRIVATE_TITLE_MARKER' not in response.text + json.dumps([original.config, record.descriptor])
    assert record.request_hash == expected


def test_document_request_keeps_existing_hash_without_guessing_filename(document_case):
    case = document_case
    key = request_id('document-no-title')
    configure_system_limits(comic_title_prompt='TITLE_SEARCH_ONLY_MARKER')
    for fields in ({'comic_title': 'PRIVATE_TITLE_MARKER'}, {}):
        assert create_document(case, key=key, filename='A Famous Manga Chapter 01.zip', **fields) == key
    with session_factory()() as db:
        jobs = db.scalars(select(Job).where(Job.owner_id == case.owner, Job.idempotency_key == key)).all()
        assert len(jobs) == 1 and 'comic_title' not in jobs[0].config
        assert all(value not in jobs[0].config['text_prompt'] for value in ('A Famous Manga', 'TITLE_SEARCH_ONLY_MARKER', 'PRIVATE_TITLE_MARKER'))
        assert jobs[0].request_hash == digest(document_body(case, filename='A Famous Manga Chapter 01.zip'))


def test_guest_replay_ignores_removed_title_context(visitor):
    key = request_id('guest-no-title')
    first = guest_submit(visitor, key=key, comic_title='PRIVATE_TITLE_MARKER')
    assert first.status_code == 202 and 'PRIVATE_TITLE_MARKER' not in first.text
    assert guest_submit(visitor, key=key).json() == first.json()


@pytest.mark.parametrize('failure', [None, 'TEXT_INVALID_RESPONSE', 'TEXT_INCOMPLETE', 'TEXT_RATE_LIMITED'])
def test_frozen_guidance_and_ocr_ids_survive_repair_and_paid_replay(text_case, monkeypatch, failure):
    job_id, lease_id = text_case
    segments = [{'id': 'comic_title', 'source': 'This is a dialogue ID, not metadata.'},
                {'id': 'b001', 'source': 'Hello!'}]
    translated = {'comic_title': '这是台词。', 'b001': '你好！'}
    with session_factory()() as db:
        frozen = dict(db.get(Job, job_id).config)
        db.get(ClassicState, job_id).analysis = analysis(segments)
        db.commit()
    configure_system_limits(comic_translation_prompt='A changed instruction only for future jobs.')
    calls = []
    bad = json.dumps({**translated, 'b001': '<think>private reasoning</think>你好！'})
    partial = '{"b001":"还没说完'

    def invoke(messages, profile, *, json_schema, limits):
        calls.append(messages)
        system = 'Translate the comic text into 简体中文 (Simplified Chinese).\nSegment count: 2\n\n' + frozen['text_prompt']
        if len(calls) == 2 and failure in page_text.RETRY_HINTS:
            system += '\n' + page_text.RETRY_HINTS[failure].format(count=2)
        assert messages[0]['content'] == system
        assert [row['role'] for row in messages] == ['system', 'user']
        assert json.loads(messages[1]['content']) == {row['id']: row['source'] for row in segments}
        assert json_schema['schema']['required'] == ['comic_title', 'b001']
        assert limits.max_output_tokens == page_text.output_token_limit(segments, profile['max_output_tokens'])
        if failure and len(calls) == 1:
            configure_system_limits(comic_translation_prompt='Changed again during format repair.')
            if failure == 'TEXT_INVALID_RESPONSE':
                return TextResponse(bad, None, 'format-repair')
            raise page_text.TextError(failure, 'fixture', retryable=True,
                                      failed_output=partial if failure == 'TEXT_INCOMPLETE' else None)
        return TextResponse(json.dumps(translated), {'input_tokens': 10, 'output_tokens': 2}, 'translated')

    monkeypatch.setattr(page_text, 'call_messages', invoke)
    monkeypatch.setattr(classic, 'call_text', page_text.call_text)
    if failure:
        with pytest.raises(page_text.TextError, match=failure):
            classic.run_text_stage(job_id, lease_id)
        with session_factory()() as db:
            assert db.get(ClassicState, job_id).translations == {}
    assert classic.run_text_stage(job_id, lease_id) == {'translations': translated}
    assert classic.run_text_stage(job_id, lease_id) == {'translations': translated}
    assert len(calls) == (2 if failure else 1)
    with session_factory()() as db:
        records = db.scalars(select(TextCall).where(TextCall.job_id == job_id).order_by(TextCall.sequence)).all()
        assert [row.error_code for row in records] == ([failure, None] if failure else [None])
        assert [row.failed_output for row in records] == (
            [{'TEXT_INVALID_RESPONSE': bad, 'TEXT_INCOMPLETE': partial}.get(failure), None] if failure else [None])
        for row, messages in zip(records, calls, strict=True):
            sent = {'messages': messages, 'response_format': {'type': 'json_schema', 'json_schema': page_text.response_schema(segments)}}
            assert row.reserved_micros == classic._cost_micros(frozen['text'], len(json.dumps(sent, ensure_ascii=False).encode()) + 256,
                page_text.output_token_limit(segments, frozen['text']['max_output_tokens']))
        assert db.get(Job, job_id).config == frozen
