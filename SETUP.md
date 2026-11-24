# Easy Protein Kitchen: setup guide

Everything in this folder is built and tested offline. What's left needs **your** accounts.
You create every account and type every password and key yourself; Claude never does.

- **GitHub:** your existing account `subuwithasuu`, repo **`subuwithasuu.github.io`** (public)
- **Website (free, test phase):** **https://subuwithasuu.github.io**

Every pin links to `subuwithasuu.github.io/recipes/...`. That address lives in exactly one place
(`site.base_url` in `config.yaml`). The posting job **refuses to post** unless
`https://subuwithasuu.github.io/privacy/` loads over HTTPS with no redirect to another site.

**Moving to your own domain later is safe:** when you add a custom domain to this repo, GitHub
301-redirects every old `subuwithasuu.github.io/...` link to it, so pins already posted keep
working. See "Moving to your own domain" at the end. Never delete or rename this repo afterwards.

## How it runs (your Mac can stay off)

| Workflow | When | What it does |
|---|---|---|
| `content.yml` | daily | Builds the first 60-recipe library (~25/day), then writes 12 more whenever unposted content drops below 3 weeks. Each recipe: Gemini writes → Gemini + Google Search fact-checks against published recipes → hard rules → **protein recalculated from the ingredients** → real Unsplash photos. |
| `post.yml` | hourly | Checks the live site, then posts whatever is due (image pins + narrated video pins) |
| `pages.yml` | after new content | Builds and deploys subuwithasuu.github.io |
| `report.yml` | Mondays 09:00 IST | Weekly summary as a GitHub Issue (GitHub emails it to you) |

Calendar: days 1-24 = 10-12 pins/day (each of the 8 boards reaches ~32 pins), then 3-5/day,
~20-25% video, until day 105. All in `config.yaml`.

**Protein honesty:** `src/nutrition.py` recalculates protein per serving from USDA values for each
ingredient (a conservative floor). A recipe claiming more than that floor + 15% is rejected, pins
show the verified number, and any bigger number in pin text is rewritten down. Meals need 25g+,
breakfast and vegetarian 20g+, snacks 10g+.

---

## The exact order

Each step is something only you can do, followed by what Claude does before the next one.

### Step 1: GitHub repo - DONE
Repo `subuwithasuu/subuwithasuu.github.io` exists, code is pushed, GitHub Pages is on,
`DRY_RUN=1` and `PINTEREST_API_BASE` (sandbox) are set, GitHub CLI is logged in.

### Step 2: Domain - SKIPPED for now (free github.io address; see the end of this guide)

### Step 3: Gemini + Unsplash keys (10 min)
1. Gemini key: aistudio.google.com/apikey
2. Unsplash key: unsplash.com/developers → Register as a developer → New Application → "Access Key" (Pexels paused new API keys in Nov 2026)
3. Save them as secrets from your own Terminal. Each command asks for the value, and the value stays hidden:
   ```bash
   gh secret set GEMINI_API_KEY --repo subuwithasuu/subuwithasuu.github.io
   ```
   ```bash
   gh secret set UNSPLASH_ACCESS_KEY --repo subuwithasuu/subuwithasuu.github.io
   ```

**Then Claude:** starts the content workflow and watches it. Claude reads the first batch of recipes,
the rejection log, the protein numbers and the photo matches, tunes the prompts if anything looks off,
and sends you sample pins and a recipe page to approve the look. The 60-recipe library builds over
~3 days on the free tier, while you do the next steps.

### Step 4: Pinterest Business account (15 min)
1. Create a **Business** account at pinterest.com/business/create with username **easyproteinkitchen**.
   Add a profile photo and bio.
2. *(Recommended)* Create the 8 boards by hand with these exact names, so the profile looks alive
   while API access is pending (the bot reuses boards with matching names):
   High Protein Dinners · High Protein Breakfast · High Protein Meal Prep · High Protein Vegetarian ·
   High Protein Snacks · 30 Minute High Protein Meals · High Protein Chicken Recipes · Budget High Protein Meals
3. Settings → Claimed accounts → Websites → claim **`subuwithasuu.github.io`** with the **HTML tag**
   option. Send Claude the `content="..."` code (it is public, not a secret).

**Then Claude:** puts the code in the `PINTEREST_VERIFY` variable and redeploys. You then click **Verify** on Pinterest.

### Step 5: Pinterest developer app (15 min)
1. developers.pinterest.com → log in with the business account → My apps → Connect app.
   - Website: `https://subuwithasuu.github.io`
   - Privacy policy: `https://subuwithasuu.github.io/privacy/`
   - Description: use the "What does your app do?" text below
2. In the app settings, add the redirect URI **`http://localhost:8085/callback`**.
3. Save the App ID and secret as secrets `PINTEREST_APP_ID` and `PINTEREST_APP_SECRET`
   (same `gh secret set ... --repo subuwithasuu/subuwithasuu.github.io` as in step 3).
4. If the app page offers a **sandbox token**, save it as secret `PINTEREST_ACCESS_TOKEN`.
5. Get your refresh token. This opens Pinterest's own consent page, where you click Allow:
   ```bash
   cd ~/"pro edit/pinterest-autopilot" && read -s "PINTEREST_APP_SECRET?App secret: " && export PINTEREST_APP_SECRET && read "PINTEREST_APP_ID?App ID: " && export PINTEREST_APP_ID && .venv/bin/python src/auth.py
   ```
   Save the printed token as secret `PINTEREST_REFRESH_TOKEN`.

**Then Claude:** runs the posting workflow against the **sandbox** and fixes anything the real API
complains about, until an image pin and a video pin both post end-to-end.

### Step 6: Token auto-renew key (5 min)
github.com/settings/personal-access-tokens → **Fine-grained** → only repo
`subuwithasuu/subuwithasuu.github.io` → permission **Secrets: Read and write** → expiry 6 months.
Save it as secret `SECRETS_PAT`. This lets the bot store the new Pinterest refresh token each time
Pinterest rotates it, so posting never stops mid-run. Because the token only covers this one repo,
your other repos stay out of reach.

### Step 7: Demo video + Standard access application (20 min)
Record your screen (QuickTime → New Screen Recording) following the script below, then submit the
Standard access request in the developer portal. Approval usually takes days to a few weeks.

**Then Claude:** prepares everything the video shows. If approval takes more than ~2 weeks, Claude can
switch on the backup (Pinterest's bulk-upload CSV with scheduled publish dates) so posting starts anyway.
Those pins link to the same final address.

### Step 8: Go live
When the approval email arrives, tell Claude. **Claude** then removes `PINTEREST_API_BASE` and
`PINTEREST_ACCESS_TOKEN`, turns `DRY_RUN` off and watches the first day of hourly runs. Day 1 of the
105-day calendar starts with the first real post.

### Keep the domain alive
**Turn on auto-renew** at the registrar. If the domain ever lapses, every pin's link breaks.

### Later
- **30-Day High-Protein Meal Plan**: when the PDF and its sales link exist, set `product.url` in
  `config.yaml`. A box then appears on every recipe page.
- **Affiliate links**: add them in `data/affiliate.yaml` (see the top of `src/build_site.py`).

---

## Standard access application: text to paste

**What does your app do?**
> A private publishing tool for my own Pinterest business account, Easy Protein Kitchen. It publishes
> original high-protein recipe pins (images and short videos I create from my own recipes and licensed
> Unsplash photography) to my own boards on a schedule, linking to the recipes on my own website
> subuwithasuu.github.io, and reads analytics for those pins for a weekly performance report.
> It does not access any other user's data and has no other users.

**Scopes:** `boards:read`, `boards:write`, `pins:read`, `pins:write`, `user_accounts:read`

**Demo video script (~2 minutes):**
1. Show the website: home page, one recipe page, the privacy-policy page.
2. In Terminal run `src/auth.py`. Show Pinterest's consent screen, click **Allow**, and show "Success"
   (crop or blur the printed token).
3. Show the repo's "Post due pins" workflow running against the sandbox, with the log line `POSTED ... pin <id>`.
4. Show the rendered pin image from that run.
5. Show a weekly report issue (how analytics are used).

---

## Moving to your own domain (when the account is working)
1. Buy the domain (e.g. easyproteinkitchen.com; .com renews cheapest - avoid .online, it renews at ~Rs3,300+/yr).
2. Add these DNS records at the registrar (Cloudflare: "DNS only"):

   | Type | Name | Value |
   |---|---|---|
   | A | @ | 185.199.108.153 / .109.153 / .110.153 / .111.153 (4 records) |
   | AAAA | @ | 2606:50c0:8000::153 / 8001 / 8002 / 8003 (4 records) |
   | CNAME | www | subuwithasuu.github.io |

3. GitHub profile Settings -> Pages -> Add a domain -> verify with the TXT record GitHub shows.
4. Claude then sets it as this repo's custom domain, enforces HTTPS, changes `site.base_url` +
   `custom_domain` in `config.yaml`, and you re-claim the new domain on Pinterest.
   GitHub redirects all old github.io links, so existing pins keep working.

## Honest limits
- The fact-check and rules cut recipe errors a lot but can't promise zero. Skim recipes in week 1.
- Protein numbers are conservative estimates from USDA averages, not lab values (the site says so).
- Photos are real Unsplash food photos matched by description. They show the dish style, not our exact plate.
- If Pinterest ever shows a spam warning, lower `ramp_pins_per_day` in `config.yaml`.
