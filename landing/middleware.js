// Vercel Edge Middleware — Accept-Language based redirect on first visit.
//
// Behavior :
//   - Visitor hits / (root)
//   - We inspect the Accept-Language header
//   - If the visitor's primary language starts with "en" → 302 redirect to /en
//   - Otherwise → serve / (French) as-is
//   - Set ``lf_lang_seen=1`` cookie so we don't re-redirect on subsequent
//     visits (avoids hijacking a manual FR/EN toggle click).
//
// Note : the static files in /en/ remain directly accessible regardless
// of Accept-Language. Middleware only impacts root URL visits.

export const config = {
  matcher: ['/((?!en|api|_next|_vercel|.*\\..*).*)'],
};

export default function middleware(request) {
  const url = new URL(request.url);

  // Already on /en → no redirect
  if (url.pathname.startsWith('/en')) {
    return;
  }

  // Visitor already made a choice → don't override
  const cookie = request.headers.get('cookie') || '';
  if (cookie.includes('lf_lang_seen=1')) {
    return;
  }

  // Inspect Accept-Language : primary tag (first 2 chars before '-' or ',')
  const accept = (request.headers.get('accept-language') || '').toLowerCase();
  const primary = accept.split(',')[0].split('-')[0].trim();

  if (primary !== 'en') {
    // Non-EN visitors : stay on French — fall through, no response
    return;
  }

  // EN visitor : build a redirect response with the cookie
  return new Response(null, {
    status: 302,
    headers: {
      Location: new URL('/en', request.url).toString(),
      'Set-Cookie': 'lf_lang_seen=1; Path=/; Max-Age=31536000; SameSite=Lax',
    },
  });
}
