import {useEffect, useState} from 'react';
import {Button, Image} from 'antd';
import {authError, errorText} from './api';
import {loadTaskImages} from './taskImagePreview';
import {time} from './ui';

type Props = {jobId: string; onUnauthorized: (message: string) => void};
type Preview = Omit<Awaited<ReturnType<typeof loadTaskImages>>, 'original' | 'translated'> & {original?: string; translated?: string};

function PreviewImages({jobId, onUnauthorized}: Props) {
  const [preview, setPreview] = useState<Preview>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(), urls: string[] = [];
    const url = (blob?: Blob) => {if (!blob) return undefined; const value = URL.createObjectURL(blob); urls.push(value); return value;};
    void loadTaskImages(jobId, controller.signal).then(data => {
      if (!controller.signal.aborted) setPreview({...data, original: url(data.original), translated: url(data.translated)});
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (authError(error)) onUnauthorized(errorText(error));
      setError(errorText(error));
    });
    return () => {controller.abort(); urls.forEach(value => URL.revokeObjectURL(value));};
  }, [jobId, onUnauthorized]);
  if (error) return <p className="error" role="alert">{error}</p>;
  if (!preview) return <p className="muted" role="status">正在读取图片…</p>;
  return <><Image.PreviewGroup><div className="task-images-grid">
    <figure><figcaption>原图</figcaption>{preview.original ? <Image src={preview.original} alt="任务原图"/> : <p className="muted">{preview.originalError}</p>}
      {preview.inputExpiresAt && <small>保留至 {time(preview.inputExpiresAt)}</small>}</figure>
    <figure><figcaption>译图</figcaption>{preview.translated ? <Image src={preview.translated} alt="任务完整译图"/> : <p className="muted">{preview.resultError}</p>}
      {preview.noText && <small>未检测到文字，译图与原图一致。</small>}
      {preview.resultExpiresAt && <small>结果保留至 {time(preview.resultExpiresAt)}</small>}</figure>
  </div></Image.PreviewGroup><p className="panel-note">点击图片放大。覆盖层译图的完整预览需要原图；已清理的图片无法恢复。</p></>;
}

export function TaskImages(props: Props) {
  const [open, setOpen] = useState(false), [revision, setRevision] = useState(0);
  return <section><h3 className="detail-heading">原图与译图</h3>
    <div className="task-images-actions"><Button onClick={() => setOpen(value => !value)}>{open ? '收起图片' : '查看原图与译图'}</Button>
      {open && <Button onClick={() => setRevision(value => value + 1)}>刷新图片</Button>}</div>
    {open && <PreviewImages key={`${props.jobId}:${revision}`} {...props}/>}</section>;
}
