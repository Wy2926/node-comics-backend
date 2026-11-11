import catalog from '../../../../contracts/extension/catalog.json';
import type {SourceSite} from '../../../../contracts/extension/site-types';

const packagedIcons = import.meta.glob<string>('../../../../contracts/extension/site-icons/*.svg', {
  eager: true, query: '?url', import: 'default',
});

export const supportedSites = (catalog as (SourceSite & {key: string; adapterId: string})[]).map(site => {
  const icon = site.icon.startsWith('/site-icons/')
    ? packagedIcons[`../../../../contracts/extension${site.icon}`] : site.icon;
  if (!icon) throw new Error(`Missing packaged site icon: ${site.key}`);
  return { ...site, icon, anchor: `site-${site.adapterId}-${site.id}` };
});
