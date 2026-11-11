import {authError, errorText, fetchResponse, request} from './api';
import {materializeResult} from '../../shared/translation-images/materialize';
import type {TranslationResult} from '../../shared/translation-images/types';

export type TaskImages = {
  original: string | null; original_error: string | null; input_expires_at: string | null;
  result: TranslationResult | null; result_error: string | null; result_expires_at: string | null;
};

export async function loadTaskImages(jobId: string, signal: AbortSignal) {
  const data = await request<TaskImages>(`/v1/admin/monitor/tasks/${encodeURIComponent(jobId)}/images`, {signal});
  const read = async (path?: string | null) => path ? (await fetchResponse(path, {signal})).blob() : undefined;
  const artifactPath = data.original || data.result?.representation === 'full-image-v1' ? data.result?.artifact?.path : undefined;
  const [source, artifact] = await Promise.allSettled([read(data.original), read(artifactPath)]);
  signal.throwIfAborted();
  for (const item of [source, artifact]) if (item.status === 'rejected' && authError(item.reason)) throw item.reason;
  const original = source.status === 'fulfilled' ? source.value : undefined;
  const originalError = source.status === 'rejected' ? errorText(source.reason) : data.original_error;
  let translated: Blob | undefined, resultError = data.result_error;
  if (data.result) {
    try {
      if (artifact.status === 'rejected') throw artifact.reason;
      if (!original && data.result.representation !== 'full-image-v1') throw Error('原图不可用，无法合成完整译图。');
      translated = await materializeResult(data.result,
        data.result.representation === 'full-image-v1' ? undefined : original, artifact.value);
    } catch (error) { resultError = errorText(error); }
  }
  signal.throwIfAborted();
  return {original, translated, originalError, resultError, inputExpiresAt: data.input_expires_at,
    resultExpiresAt: data.result_expires_at, noText: data.result?.kind === 'no_text'};
}
