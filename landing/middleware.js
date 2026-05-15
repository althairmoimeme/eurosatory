// Vercel Edge Middleware — Accept-Language based redirect on first visit.
//
// Behavior :
//   - Visitor lands on / (root)
//   - We inspect the Accept-Language header
//   - If the visitor's primary language starts with "en" → 302 redirect to /en
//   - Otherwise → serve / (French) as-is
//   - We set a cookie ``lf_lang_seen=1`` after first visit so we don't
//     re-redirect (avoids hijacking a manual click on the FR/EN toggle).
//
// Note : on Vercel the middleware runs ONLY at the edge ; the static files
// in /en/ remain directly accessible regardless of Accept-Language.

export const config = {
  // Match the root path only — NEVER /en, /api, /_next, /favicon, assets
  matcher: ['/((?!en|api|_next|_vercel|.*\\..*).*)'],
};

export default function middleware(request) {
  const { pathname } = new URL(request.url);

  // Already on /en → no redirect (let the static handler serve it)
  if (pathname.startsWith('/en')) return;

  // Visitor has already been seen / made a choice → don't override
  const cookie = request.headers.get('cookie') || '';
  if (cookie.includes('lf_lang_seen=1')) return;

  // Inspect Accept-Language
  const accept = (request.headers.get('accept-language') || '').toLowerCase();
  // Primary language tag = first 2 chars before any '-' or ','
  const primary = accept.split(',')[0].split('-')[0].trim();

  // Build the redirect response
  const response = primary === 'en'
    ? Response.redirect(new URL('/en', request.url), 302)
    : undefined;

  // Set the "seen" cookie on any response we return
  if (response) {
    response.headers.set(
      'Set-Cookie',
      'lf_lang_seen=1; Path=/; Max-Age=31536000; SameSite=Lax',
    );
    return response;
  }

  // Otherwise let it pass through (will serve / = French)
  return;
}
