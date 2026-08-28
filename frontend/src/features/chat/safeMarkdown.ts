import DOMPurify from 'dompurify';
import MarkdownIt from 'markdown-it';

const markdown = new MarkdownIt({
  html: true,
  breaks: true,
  linkify: true,
  typographer: false,
});
markdown.validateLink = () => true;

const FORBIDDEN_TAGS = [
  'script',
  'iframe',
  'object',
  'embed',
  'form',
  'input',
  'button',
  'textarea',
  'select',
  'option',
  'style',
  'svg',
  'math',
];

export function isSafeContentUrl(raw: string): boolean {
  const value = raw.trim();
  if (!/^https?:\/\//i.test(value) || value.startsWith('//')) return false;

  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function normalizeUrls(fragment: DocumentFragment): void {
  fragment.querySelectorAll(FORBIDDEN_TAGS.join(',')).forEach((element) => element.remove());

  fragment.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((link) => {
    const raw = link.getAttribute('href')?.trim() ?? '';
    if (!isSafeContentUrl(raw)) {
      link.removeAttribute('href');
      return;
    }

    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  });

  fragment.querySelectorAll<HTMLImageElement>('img[src]').forEach((image) => {
    const raw = image.getAttribute('src')?.trim() ?? '';
    if (!isSafeContentUrl(raw)) image.removeAttribute('src');
    image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
  });
}

export function renderSafeMarkdown(source: string): string {
  const sanitized = DOMPurify.sanitize(markdown.render(source), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: FORBIDDEN_TAGS,
    FORBID_ATTR: ['style', 'srcdoc', 'formaction'],
  });
  const template = document.createElement('template');
  template.innerHTML = sanitized;
  normalizeUrls(template.content);
  return template.innerHTML;
}
