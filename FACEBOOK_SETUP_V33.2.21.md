# Facebook Page publishing — setup V33.2.21

This change has been merged into `main` by PR #1. Render may deploy automatically when connected to `main`; the actual deployment status must be confirmed in the Render dashboard before testing.

## Render variables

In the Render service `sthetcar-ia`, preserve existing environment values and add/check only:

- `FACEBOOK_REDIRECT_URI=https://sthetcar-ia.onrender.com/auth/facebook/callback`
- `FACEBOOK_PAGE_ID=110352814889450`
- `FACEBOOK_LOGIN_CONFIG_ID=1807068463657763`
- `PUBLIC_BASE_URL=https://sthetcar-ia.onrender.com`

The code reuses `META_APP_ID`, `META_APP_SECRET`, and `META_GRAPH_VERSION` from the existing Meta app. Do not paste access tokens into Render or into this repository.

## Meta app

In Meta Developers for app “Sthetcar IA”:

1. Under **Login do Facebook para Empresas → Configurações**, keep the valid OAuth redirect URI saved as exactly:

   `https://sthetcar-ia.onrender.com/auth/facebook/callback`

2. The configuration **Sthetcar IA - Página Facebook** has been created with ID `1807068463657763`, token type **system-user access token** with 60-day expiration, the Pages asset class marked as required, and permissions `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`.

The app's OAuth request uses this Business Login `config_id`; it does not pass a `scope` query parameter. Keep the existing Instagram OAuth redirect URI and settings unchanged.

## Safe validation sequence after review and deployment

1. In Render, confirm the deployment from the merged `main` commit is **Live** before using it. The merge itself does not prove the Render deployment succeeded.
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

The Python syntax/template GitHub Actions check passed before merge. That is a static check only: it does not verify Meta OAuth, Render environment variables, Supabase runtime behavior, or actual publishing. Confirm Render is Live, add the required variables safely, connect and validate Facebook, then perform a Facebook-only test post before using combined publication.
