import { ErrorResponse, User } from 'oidc-client-ts';

export const sessionPrefix = 'nc-site-session:';
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}
export class RefreshUnavailable extends Error {
  constructor(reason: RefreshFailure = 'service') {
    super(reason === 'network' ? '网络连接失败，请检查连接后重试。' : reason === 'configuration' ?
      '正式登录服务尚未配置，请稍后重试或联系支持。' : '账户服务暂时不可用，请重试。');
  }
}
type RefreshFailure = 'network' | 'configuration' | 'service';
interface StoredSession { id: string; user: string | null; retryAt?: number; retryReason?: RefreshFailure; accessRejected?: boolean; relogin?: boolean }
interface SessionBinding { id: string; user: User }
export const changedSession = () => new ApiError('账户已退出，请重新登录。', 401);

export function validUser(user: User): boolean {
  return !!user.access_token && !!user.refresh_token && user.token_type?.toLowerCase() === 'bearer' &&
    Number.isFinite(user.expires_at) && user.expires_at! > 0 && typeof user.profile?.sub === 'string' && !!user.profile.sub;
}

// One atomic record is the only durable token store. Protocol operations do not
// write it: a short write lock commits their result only to its original login.
export class SharedSession {
  constructor(
    private storage: Storage,
    readonly key: string,
    private locks: Pick<LockManager, 'request'>,
    private refresh: (user: User) => Promise<User>,
    private notify: () => void,
  ) {}

  private read(): StoredSession {
    try {
      const value = JSON.parse(this.storage.getItem(this.key) ?? 'null');
      if (typeof value?.id === 'string' && (value.user === null || typeof value.user === 'string')) return value;
    } catch { /* Invalid persisted data is not an identity. */ }
    return { id: '', user: null };
  }

  private write(value: StoredSession, notify = false) {
    this.storage.setItem(this.key, JSON.stringify(value));
    if (notify) this.notify();
  }

  private user(value: StoredSession): User | null {
    if (!value.user) return null;
    try { const user = User.fromStorageString(value.user); return validUser(user) ? user : null; }
    catch { return null; }
  }

  assert(id: string) { if (this.read().id !== id) throw changedSession(); }

  async beginLogin() {
    return this.locks.request(this.key + ':write', () => {
      const prompt = this.read().relogin ? 'login consent' : 'consent';
      const id = crypto.randomUUID();
      this.write({ id, user: null }, true);
      return { id, prompt };
    });
  }

  async finishLogin(id: string, user: User) {
    if (!validUser(user) || user.expired) throw changedSession();
    return this.locks.request(this.key + ':write', () => {
      const current = this.read();
      if (current.id !== id || current.user !== null) throw changedSession();
      this.write({ id, user: user.toStorageString() }, true);
    });
  }

  async signOut() {
    await this.locks.request(this.key + ':write', () => {
      this.write({ id: crypto.randomUUID(), user: null, relogin: true }, true);
    });
  }

  async expire(binding: SessionBinding) {
    await this.locks.request(this.key + ':write', () => {
      const current = this.read();
      if (current.id === binding.id && this.user(current)?.access_token === binding.user.access_token)
        this.write({ id: crypto.randomUUID(), user: null }, true);
    });
  }

  async get(rejectedToken?: string, expectedId?: string): Promise<SessionBinding | null> {
    const initial = this.read();
    if (expectedId !== undefined && initial.id !== expectedId) throw changedSession();
    const first = this.user(initial);
    if (!first) return null;
    const needsRefresh = (user: User, record: StoredSession) => record.accessRejected || user.expired || (user.expires_in ?? 0) < 60 ||
      rejectedToken === user.access_token;
    if (!needsRefresh(first, initial)) return { id: initial.id, user: first };
    return this.locks.request(this.key + ':refresh', async () => {
      const before = this.read();
      if (before.id !== initial.id) throw changedSession();
      const user = this.user(before);
      if (!user) return null;
      const binding = { id: before.id, user };
      if (!needsRefresh(user, before)) return binding;
      const rejected = !!before.accessRejected || rejectedToken === user.access_token;
      if ((before.retryAt ?? 0) > Date.now()) {
        if (!user.expired && !rejected) return binding;
        if (rejected && !before.accessRejected) await this.locks.request(this.key + ':write', () => {
          this.assert(before.id); this.write({ ...before, accessRejected: true });
        });
        throw new RefreshUnavailable(before.retryReason);
      }
      let renewed: User;
      try {
        renewed = await this.refresh(user);
        if (!validUser(renewed) || renewed.expired) throw new RefreshUnavailable();
        if (renewed.profile.sub !== user.profile.sub) throw changedSession();
      } catch (error) {
        this.assert(before.id);
        if (error instanceof ApiError || error instanceof ErrorResponse &&
          ['invalid_grant', 'login_required', 'interaction_required', 'consent_required', 'account_selection_required'].includes(error.error ?? '')) {
          await this.expire(binding);
          throw changedSession();
        }
        const reason = error instanceof TypeError || error instanceof Error && error.name === 'ErrorTimeout' ? 'network' :
          error instanceof ErrorResponse && ['invalid_client','unauthorized_client','invalid_scope','invalid_target','invalid_request'].includes(error.error ?? '') ? 'configuration' : 'service';
        await this.locks.request(this.key + ':write', () => {
          const current = this.read();
          if (current.id === before.id && current.user === before.user)
            this.write({ ...current, retryAt: Date.now() + 30000, retryReason: reason, accessRejected: rejected });
        });
        this.assert(before.id);
        if (!user.expired && !rejected) return binding;
        throw new RefreshUnavailable(reason);
      }
      return this.locks.request(this.key + ':write', () => {
        const current = this.read();
        if (current.id !== before.id || current.user !== before.user) throw changedSession();
        this.write({ id: before.id, user: renewed.toStorageString() });
        return { id: before.id, user: renewed };
      });
    });
  }
}
