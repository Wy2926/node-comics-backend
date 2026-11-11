export type SourceSiteContentTag = 'western' | 'manga' | 'manhwa' | 'manhua' | 'webtoon' | 'doujin' | 'illustration';
export type SourceSiteAccessTag = 'login-required' | 'partial-web';
export type SourceSitePricing = 'free' | 'freemium' | 'unknown';
export interface SourceSite {
  id: string;
  name: string;
  url: string;
  /** Packaged icon URL; no third-party image requests when opening the directory. */
  icon: string;
  /** Main content languages (BCP 47), in display order; not an exhaustive catalog filter. */
  primaryLanguages: readonly string[];
  /** First supported date, YYYY-MM-DD; independent of later adapter fixes. */
  adaptedOn: string;
  /** Reading charges, independent of optional account extras; unknown is not free. */
  pricing: SourceSitePricing;
  /** Primarily untranslated Japanese/Korean originals, not inferred from the UI language. */
  isRaw: boolean;
  /** Confirmed reading conditions affecting at least part of the site's comics; not account extras. */
  accessTags?: readonly SourceSiteAccessTag[];
  /** Confirmed main content types; independent of pricing, language and reading conditions. */
  contentTags: readonly SourceSiteContentTag[];
  /** Implemented name-search protocol. */
  search?: true;
  /** Verified source page size when it exceeds the default search result budget; hard capped by the runtime. */
  searchPageSize?: number;
}
