# PawPicks Session State
_Auto-maintained. Load this at the start of each session._

## Project
- **Site:** PawPicks — pet accessories affiliate site
- **Repo:** https://github.com/DMoneyOH/pawpicks
- **Live URL:** https://DMoneyOH.github.io/pawpicks (pending Pages enable)
- **Local path:** /home/derek/projects/pawpicks

## Status
- [x] Phase 0: Niche selected (pet care, Amazon Associates + Chewy + Petco)
- [x] Phase 1: Site scaffold built and pushed to GitHub main
- [x] Desktop Commander: bash shell, allowedDirectories scoped, fileWriteLineLimit 150
- [x] gh CLI installed (v2.67.0), authenticated as DMoneyOH
- [x] multi-ai-collab MCP registered (Gemini key present in credentials.json)
- [ ] Phase 1b: GitHub Pages + branch protection still needed
- [ ] Phase 2: Content generation (generate_posts.py ready, needs GEMINI_API_KEY in .env)
- [ ] Phase 3: Affiliate program applications (Amazon Associates, Chewy, Petco)
- [ ] Phase 4: SEO baseline + Search Console submission
- [ ] Phase 5: Cron job install

## Blockers
- GitHub push needs `workflow` scope token — re-auth code F96C-D820 was issued,
  awaiting user browser approval OR we use gh API to push workflow separately.
- GEMINI_API_KEY needs to be written to /home/derek/projects/pawpicks/.env

## Key Files
- generate_posts.py — Gemini content generator
- autopublish.sh — weekly cron wrapper
- .github/workflows/deploy.yml — GitHub Actions auto-deploy

## Credentials (locations only, never values)
- Gemini API key: /home/derek/.claude-mcp-servers/multi-ai-collab/credentials.json
- GitHub token: /home/derek/.config/gh/hosts.yml
- Bitwarden CLI available for any new secrets

## Next Actions
1. User approves workflow scope (browser) OR I push via gh API
2. Enable GitHub Pages (gh API call — no browser needed)
3. Set branch protection rules on main
4. Write .env with Gemini key
5. Run generate_posts.py — 10 articles
6. Install cron job
