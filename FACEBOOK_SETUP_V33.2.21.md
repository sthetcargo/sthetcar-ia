# Facebook Page publishing — setup V33.2.21

This change is isolated to branch `feat/facebook-page-publishing-v33-2-21`. It has **not** been merged into `main` or deployed to Render.

## Render variables

In the Render service `sthetcar-ia`, preserve existing environment values and add/check only:

- `FACEBOOK_REDIRECT_URI=https://sthetcar-ia.onrender.com/auth/facebook/callback`
- `FACEBOOK_PAGE_ID=110352814889450`
- `PUBLIC_BASE_URL=https://sthetcar-ia.onrender.com`

The code reuses `META_APP_ID`, `META_APP_SECRET`, and `META_GRAPH_VERSION` from the existing Meta app. Do not paste access tokens into Render or into this repository.

## Meta app

In Meta Developers for app “Sthetcar IA”, add the exact redirect URI below under the valid OAuth redirect URIs for the Facebook Login product used by the app:

`https://sthetcar-ia.onrender.com/auth/facebook/callback`

Keep the existing Instagram redirect URI. Do not remove Instagram scopes or change its callback.

## Safe validation sequence after review and deployment

1. Confirm the deployment is the expected branch/commit before using it. Do not merge into `main` or deploy until code review and tests are complete.
2. Sign in to Sthetcar IA and open the Instagram + Facebook publication center.
3. Click **Conectar Página do Facebook**. Complete Meta authorization in the same browser session.
4. Verify the page name and click **Validar conexão Facebook**.
5. Choose one approved test post whose vehicle, service, photos, caption, and hashtags match exactly.
6. For Facebook only, publish a clearly identified test post and verify it appears on the intended Page. For Instagram, require the existing media container to report `FINISHED` before including Instagram in a combined publication.
7. Review the per-platform result and audit before any repeat attempt. Never repeat a platform marked published.

All publication endpoints cause real external posts. The new combined flow checks the selected destinations before starting, but remote APIs cannot provide a fully atomic two-platform transaction: one platform can succeed while the other fails. Each platform has a separate audit to make this visible.

## State and storage

The new Supabase state keys `facebook_connection` and `facebook_publication_audit` are optional until the Facebook connection is used. Starting the app should not need a schema migration or populate these keys. The Facebook page token is kept in the server's private state and is never rendered in the page.

## Current validation boundary

The branch has been created for review only. Meta OAuth and any real Facebook/Instagram publication must be tested against the Render environment before this change is considered production-ready. Do not make a real post during code review.
