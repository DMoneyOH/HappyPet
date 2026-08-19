# HappyPet

## Push classification
`main` has an automated consumer — GitHub Actions `publish.yml` fires on a Mon/Thu cron and on a push of `_posts/DRAFT-*.md`; it pushes to `main` itself, then dispatches `deploy.yml` (live GitHub Pages publish) and `pin.yml` (auto-post), with no human-gated step in between. Per the fleet autonomy charter (`obsidian\wiki\governance\autonomy_charter.md`, Appendix A), a push here is **Class 3**: get an explicit go-ahead before pushing.
