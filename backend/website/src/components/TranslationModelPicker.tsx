import {useEffect, useRef} from 'react';
import type {TranslationModelChoice} from '../../../shared/translation-models';
import {translationModelChoices} from '../../../shared/translation-models';
import type {ModelCopy} from '../i18n/translation-models';

const iconPaths = {
  automatic: 'm12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Z M20 2v4 M18 4h4',
  free: 'M20 4c-8-1-15 3-15 9a6 6 0 0 0 6 6c6 0 10-7 9-15Z M4 21 15 10 M9 16v-5',
  paid: 'm7 4-4 6 9 11 9-11-4-6H7Z M3 10h18 M7 4l5 17 5-17',
  unknown: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4 M12 16v.1',
};
function ModelIcon({kind}: {kind: keyof typeof iconPaths}) {
  return <span className="translation-model-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" focusable="false"><path d={iconPaths[kind]}/></svg></span>;
}

export default function TranslationModelPicker({
  models,
  value,
  onChange,
  copy: t,
  upgradeUrl,
  disabled = false,
}: {
  models?: TranslationModelChoice[];
  value?: string;
  onChange(value?: string): void;
  copy: ModelCopy;
  upgradeUrl: string;
  disabled?: boolean;
}) {
  const root = useRef<HTMLDetailsElement>(null),
    selected = models?.find((model) => model.id === value);
  const choices = translationModelChoices(models, t.automatic, value);
  const selectedKind = !value ? 'automatic' : !selected ? 'unknown' : selected.requires_paid ? 'paid' : 'free';
  useEffect(() => { if (disabled && root.current) root.current.open = false; }, [disabled]);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (root.current?.open && event.target instanceof Node && !root.current.contains(event.target))
        root.current.open = false;
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, []);
  return (
    <div className="translation-model-picker">
      <span>{t.label}</span>
      <details
        ref={root}
        onToggle={({currentTarget: menu}) => {
          if (!menu.open) return;
          const bounds = menu.getBoundingClientRect();
          const headerBottom = document.querySelector('.site-header')?.getBoundingClientRect().bottom ?? 0;
          const above = bounds.top - Math.max(16, headerBottom + 8);
          const below = window.innerHeight - bounds.bottom - 16;
          const panel = menu.querySelector<HTMLElement>('.translation-model-options')!;
          const height = Math.min(panel.scrollHeight + 2, 400, window.innerHeight * .6);
          const opensAbove = below < height && above > below;
          menu.dataset.placement = opensAbove ? 'above' : 'below';
          menu.style.setProperty('--model-menu-space', `${Math.max(0, opensAbove ? above : below)}px`);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && root.current?.open) {
            event.preventDefault();
            event.stopPropagation();
            root.current.open = false;
            root.current.querySelector('summary')?.focus();
          }
        }}
      >
        <summary data-kind={selectedKind} aria-label={t.label} aria-disabled={disabled} tabIndex={disabled ? -1 : 0}
          onClick={event => { if (disabled) event.preventDefault(); }}>
          <ModelIcon kind={selectedKind}/>
          <span className="translation-model-name">{value ? (selected?.name ?? value) : t.automatic}</span>
          {selected?.quota_multiplier != null && <span className="translation-model-rate">{selected.quota_multiplier}×</span>}
        </summary>
        <div className="translation-model-options" role="group" aria-label={t.label}>
          {choices.map((model) => {
            const kind = !model.id ? 'automatic' : model.id === value && !selected ? 'unknown' : model.requires_paid ? 'paid' : 'free';
            const active = (value ?? '') === model.id;
            const content = (
              <>
                <ModelIcon kind={kind}/>
                <span className="translation-model-copy">
                  <strong>{model.name}</strong>
                  <span className="translation-model-badges">
                    {model.id && kind !== 'unknown' && (
                      <small className="translation-model-tier">{model.requires_paid ? t.paid : t.free}</small>
                    )}
                    <small className="translation-model-status">{model.available ? t.available : t.unavailable}</small>
                  </span>
                </span>
                <span className="translation-model-meta">
                  {model.quota_multiplier != null && <span className="translation-model-rate">{model.quota_multiplier}×</span>}
                  {!model.available ? <span className="translation-model-upgrade">{t.upgrade} ↗</span> : active && (
                    <svg className="translation-model-check" viewBox="0 0 20 20" fill="none" aria-hidden="true" focusable="false">
                      <circle cx="10" cy="10" r="9" fill="currentColor"/>
                      <path d="m6 10 2.5 2.5L14 7" stroke="var(--surface)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </span>
              </>
            );
            return (
              <div
                className="translation-model-option"
                key={model.id}
                data-available={model.available}
                data-kind={kind}
              >
                {model.available ? (
                  <button
                    className="translation-model-choice"
                    type="button"
                    disabled={disabled}
                    aria-pressed={active}
                    onClick={() => {
                      onChange(model.id || undefined);
                      root.current!.open = false;
                      root.current!.querySelector('summary')?.focus();
                    }}
                  >
                    {content}
                  </button>
                ) : (
                  <a className="translation-model-choice" href={upgradeUrl} target="_blank" rel="noopener noreferrer">
                    {content}
                  </a>
                )}
              </div>
            );
          })}
        </div>
      </details>
    </div>
  );
}
