# DPRK News Aggregator — Project Context & Knowledge Base

## 🎯 What This Is
A **DPRK-only news aggregator website** that pulls curated news from sources that exclusively report on North Korea. It's a static HTML site deployed to Cloudflare Pages (free, no credit card needed).

---

## 📁 Where Everything Lives
| Item | Location |
|---|---|
| **Project files** | `C:/Users/Beached/hermes/dpknews/` |
| **GitHub repo** | `https://github.com/Beached89/dpk-news-aggregator` (main branch) |
| **Live site** | `dpk-news-aggregator.<account>.workers.dev` |
| **Git credentials** | `~/.git-credentials` (fine-grained PAT for Beached89 account) |

---

## 🏗️ Architecture & Tech Stack
- **Plain HTML** — no framework, no build step, no dependencies
- **Tailwind CSS via CDN** (`cdn.tailwindcss.com`) — loaded directly in `<script>` tag
- **Google Fonts** (Inter) — loaded via `@import` in `<style>` block
- **Responsive design** — mobile hamburger menu, scaled headlines, touch-friendly cards
- **Cloudflare Pages** — auto-deploys from `main` branch on every push

---

## 📰 Sources (DPRK-Specific Only)
These are the ONLY sources allowed. No HRW, no AP, no Reuters, no general international news:
1. **KCNA** (Korean Central News Agency) — official DPRK state media
2. **Rodong Sinmun** — official party newspaper of the Workers' Party of Korea
3. **NK News** — independent specialist outlet focused exclusively on North Korea
4. **38 North** — expert analysis on DPRK military/nuclear issues (from Stimson Center)

---

## 🎨 Design: "Minimalist Newsroom" Template
Inspired by Rivax Studio's Newsroom template. Key characteristics:
- Clean, low-clutter layout
- Slate/gray color palette with white cards
- Grid-based news card layout
- Category badges (POLITICS, MILITARY, ECONOMY)
- Source attribution and timestamps on every item
- **Mobile responsive** — hamburger menu, scaled text, touch targets

---

## 🔧 How to Work On This Project

### Making Changes
```bash
cd C:/Users/Beached/hermes/dpknews
# Edit index.html (or add files)
git add .
git commit -m "Descriptive commit message"
git push origin main  # triggers Cloudflare deploy automatically
```

### Version Bumps (to trigger builds without content changes)
Add a version comment to the top of `index.html`:
```html
<!-- Version: X.Y.Z -->
```
Then commit and push. This is the accepted engineering practice — no dummy commits.

### Mobile Testing
The site uses Tailwind's responsive breakpoints:
- `md:` (768px) — tablet breakpoint, hamburger menu hides/shows nav
- `lg:` (1024px) — desktop grid layout kicks in
- Test by resizing browser or using DevTools device emulation

---

## 🚫 What NOT To Do
- ❌ Don't add non-DPRK news sources (HRW, AP, Reuters, etc.)
- ❌ Don't use dummy commits to trigger builds — use version bumps instead
- ❌ Don't try `browser_snapshot` on Cloudflare dashboard — it crashes. Use native vision or manual navigation.
- ❌ Don't embed tokens in URLs long-term — they're stored in `~/.git-credentials`
- ❌ Don't modify another profile's files (this agent runs from `kim-jong-newso`)

---

## 📋 Current Status & Next Steps

### ✅ Completed
- [x] Mobile-responsive template deployed
- [x] GitHub account switched to Beached89
- [x] Cloudflare Pages connected to new repo
- [x] Git credentials configured for automated pushes
- [x] Project moved to `hermes/dpknews/` folder

### 🔲 TODO (When You're Ready)
1. **Replace placeholder content** with real DPRK news headlines and summaries
2. **Set up automated scraping** — pull actual articles from KCNA, Rodong Sinmun, NK News, 38 North RSS feeds or APIs
3. **Add more categories** if needed (Human Rights, Diplomacy, etc.)
4. **Custom domain** — if you buy a domain later, add it to Cloudflare Pages settings

---

## 🔑 Important Notes for Future Sessions
- This agent is `@kim-jong-newso` — always check which profile/session you're in
- The GitHub token has fine-grained access scoped to ONLY this repo (Contents R/W, Metadata Read, PRs R/W, Issues R/W)
- Cloudflare Pages auto-deploys on push to `main` — no manual deployment needed
- If the site isn't updating after a push, wait 2-3 minutes for Cloudflare to rebuild

---

## 🆘 Troubleshooting Quick Reference

| Problem | Solution |
|---|---|
| Site not updating after push | Wait 2-3 min; check Cloudflare Pages "Deploys" tab for build status |
| Git push fails with 403 | Token expired or revoked — generate new PAT from GitHub settings |
| browser_snapshot hangs on Cloudflare | Use native vision instead, or navigate manually in preview pane |
| Can't find project folder | It's at `C:/Users/Beached/hermes/dpknews/` — not in tmp or Downloads |

---

*Last updated: October 1, 2024*
*Agent: @kim-jong-newso (DPRK News)*
