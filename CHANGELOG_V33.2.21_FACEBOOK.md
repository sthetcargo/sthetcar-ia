# V33.2.21 — Facebook Page publishing (review branch)

- Adds a separate Facebook Page OAuth callback using the existing Meta app credentials.
- Saves the Page token and connection metadata in private server state; never renders the token.
- Adds single-photo and multi-photo publishing to the configured Facebook Page, with separate publication audit records.
- Adds a combined platform form. Connected, eligible destinations are selected by default; the user must still confirm a real publish.
- Checks the selected destinations before the first remote publish and reports each platform result independently.
- Tightens Instagram draft matching so the saved draft must match the selected approved post's identity, caption, hashtags, vehicle, and service.
- New Supabase keys for Facebook default safely until the feature is used; no schema migration is intended.
- Does not add video publishing for Facebook; the existing Instagram video behavior is left unchanged.

## Status

This change is on `feat/facebook-page-publishing-v33-2-21` for review. It has not been merged or deployed. OAuth in Meta and real publication against Render remain unverified.
