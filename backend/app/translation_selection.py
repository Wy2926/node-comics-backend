"""Public model choices share the same locked entitlement decisions as admission."""
from .classic_config import profile_snapshot
from .adapters.text import system_prompt
from .billing_access import first_purchase
from .config import settings
from .entitlements import UNLIMITED, admission_policy, free_model_policy, membership_benefits, membership_choices, requires_paid
from .errors import problem
from .models import now
from .providers import digest
from .translation_models import TranslationProvider
from .translation_providers import _provider_query
from .translation_routing import choose_provider
from .system_settings import get_request_limits


class ModelAccess:
    """One account/time snapshot; quota lookups are shared by models with the same scope and price."""
    def __init__(self, db, user, at=None):
        self.db, self.user, self.at = db, user, at or now()
        self.guest = user is None or user.kind == 'guest'
        self.choices = [] if self.guest else membership_choices(db, user, self.at)
        self.benefits = None if self.guest else membership_benefits(db, user, self.at, choices=self.choices)
        self.policies = {}
        self.services = {}

    def paid_service(self, plans=None):
        """Document admission needs a matching right, not a reservation before page count is known."""
        if self.guest:
            return None
        scope = tuple(sorted(plans)) if plans is not None else None
        if scope not in self.services:
            if scope is not None and self.services.get(None) in scope:
                return self.services[None]
            benefits = self.benefits if scope is None else membership_benefits(self.db, self.user, self.at,
                choices=self.choices, service_plans=scope)
            service = benefits['service_plan'] if benefits['paid'] else None
            if service is None:
                pack = first_purchase(self.db, self.user.id, self.at, service_plans=scope)
                service = pack[1].service_plan_id if pack else None
            self.services[scope] = service
        return self.services[scope]

    def preferred_service(self):
        if self.choices:
            return admission_policy(self.db, self.user, at=self.at, benefits=self.benefits).service_plan
        return 'guest' if self.guest else self.paid_service() or 'free'

    def resolve(self, row):
        plans, amount = row.text_plan_ids, row.quota_multiplier
        if self.guest:
            allowed = plans is None or 'guest' in plans
            return (admission_policy(self.db, self.user, at=self.at) if self.user and allowed else None,
                    None if allowed else 'not_allowed')
        free = not requires_paid(plans)
        key = (None if free else tuple(sorted(plans)), amount)
        if key not in self.policies:
            policy = admission_policy(self.db, self.user, at=self.at, benefits=self.benefits,
                                      service_plans=None if free else plans, choices=self.choices, amount=amount)
            if free:
                policy = free_model_policy(self.db, self.user, policy, self.at, amount=amount)
            reason = ('not_allowed' if policy.kind == 'unavailable' else
                      'quota_exhausted' if policy.kind != UNLIMITED and policy.period is None else None)
            self.policies[key] = policy, reason
        return self.policies[key]


def model_routes(rows, access, routing_key, *, preferred=None):
    """The last route is for new work; an exhausted original can still serve an owned cache hit."""
    def preferred_pool(pool):
        return [row for row in pool if row.text_plan_ids is None or preferred in row.text_plan_ids] if preferred else pool

    def pick(pool):
        selected = choose_provider({row.id: row.weight for row in pool}, routing_key)
        return next(row for row in pool if row.id == selected)

    preferred_rows = preferred_pool(rows)
    routes = []
    if preferred_rows:
        original = pick(preferred_rows)
        reason = access.resolve(original)[1]
        if reason is None:
            return [original]
        if reason == 'quota_exhausted':
            routes.append(original)
    payable = [row for row in rows if access.resolve(row)[1] is None]
    if payable:
        routes.append(pick(preferred_pool(payable) or payable))
    elif not routes:
        allowed = [row for row in rows if access.resolve(row)[1] != 'not_allowed']
        if allowed:
            routes.append(pick(allowed))
    return routes


def model_catalog(db, user):
    # Public reads neither load upstream configurations/credentials nor probe models.
    rows = db.execute(_provider_query().with_only_columns(
        TranslationProvider.id, TranslationProvider.name, TranslationProvider.text_plan_ids, TranslationProvider.quota_multiplier)
        .order_by(TranslationProvider.quota_multiplier, TranslationProvider.created_at, TranslationProvider.id)).all()
    access = ModelAccess(db, user) if rows and settings().classic_enabled else None
    models = [{'id': row.id, 'name': row.name,
             'quota_multiplier': float(row.quota_multiplier),
             'requires_paid': requires_paid(row.text_plan_ids),
             'available': reason is None, 'unavailable_reason': reason}
            for row in rows
             for reason in [access.resolve(row)[1] if access else 'unavailable']]
    enabled = any(item['unavailable_reason'] in (None, 'quota_exhausted') for item in models)
    return models, enabled


def image_models(db, user, model_id, at, routing_key):
    if not settings().classic_enabled:
        problem('CLASSIC_NOT_CONFIGURED', '常规翻译引擎尚未启用', 503)
    access = ModelAccess(db, user, at)
    if model_id:
        row = db.execute(_provider_query().where(TranslationProvider.id == model_id)).first()
        if row is None:
            if db.get(TranslationProvider, model_id) is None:
                problem('TRANSLATION_MODEL_INVALID', '未知翻译模型，请刷新模型列表', 422)
            problem('TRANSLATION_MODEL_UNAVAILABLE', '所选模型暂不可用，请重新选择', 503)
        routes = [row]
    else:
        rows = db.execute(_provider_query()).all()
        routes = model_routes(rows, access, routing_key, preferred=access.preferred_service() if len(rows) > 1 else None)
        if not routes:
            problem('TRANSLATION_PROVIDER_UNAVAILABLE', '当前权益没有可用的正文翻译模型', 503)
    prompt = system_prompt(get_request_limits(db).comic_translation_prompt)
    for row in routes:
        config = profile_snapshot({'provider_id': row.id, 'revision_id': row.revision_id,
                                   'channel': row.channel, **row.config}, text_prompt=prompt)
        policy, reason = access.resolve(row)
        yield {**config, 'version': digest(config)}, row, policy, reason
