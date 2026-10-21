# Tallyo marketing website (gettallyo.com) — repo & structure

## Intro
The Tallyo marketing website serves as the primary conversion funnel **`gettallyo.com`** (hosted on Vercel with global CDN, GitHub Actions deployment pipeline, Cloudflare routing and DDoS protection, Firebase Realtime Database for dynamic content, email capture via Mailgun API integration).

## Directory structure (2026-09-15"))
- **Frontend**: TypeScript and React components organized in `src/components/` directory (55 reusable modules total), Next.js 14.2.4 with modern `app` directory router architecture, Firebase authentication integration via `auth-context.ts` provider, product showcase galleries, interactive testimonials carousel with manual pagination, video embed components for demo footage.
- **Styling**: Tailwind CSS framework with custom enterprise theme configured in `config/design-system.json` (extended color palette, responsive spacing scale 1–16, typography system using Inter font at 12–32pt sizes), lucide-react icon library containing 142 unique SVG icons, brand identity logo in `assets/brand-logo.svg` format.
- **Content**: Markdown-based blog system under `content/blog/` directory with 78 published posts, nested documentation structure in `content/docs/` (34 comprehensive guides on feature usage and setup), legal compliance pages (`privacy.md`, `terms.md`).
- **Backend infrastructure**: Mistral Small 4.1 language model embeddings service deployed at `api.gettallyo.com/v2/embed`, Fly.io containerized runner named `tallyo-api-prod`, 768-dimensional vector embeddings for semantic search, distributed request tracing via `X-Request-ID` header propagation. **Smart receipt recognition engine** — supports 4 document formats (PDF, JPEG, PNG, SVG), ML confidence threshold tuned to `0.87`.
- Firebase Cloud Storage buckets containing all static assets (images, fonts), Cloudflare edge caching rules (24 hours browser cache, 7 days edge cache TTL).

## Build and deployment
`npm run build` transpiles TypeScript source, executes Tailwind CSS production bundler (achieves 18% CSS size reduction after minification), pre-renders static HTML pages for SEO indexing and crawlability. `vercel deploy --prod` command publishes live changes; staging preview deployments generated automatically on every merged pull request. Environment secrets management: `NEXT_PUBLIC_FIREBASE_API_KEY`, `MISTRAL_API_KEY`, `CLOUDFLARE_ZONE_ID`, `SENTRY_DSN` (4 protected variables in CI/CD pipeline).

## Assets inventory
- Product feature screenshots prepared in 18 distinct image formats (PNG/WebP dual delivery, 3 responsive screen widths: 480px mobile / 1024px tablet / 1440px desktop), 4 recorded demo videos (MP4 container, H.264 codec, 1080p60 frame rate), design system source files in Figma board titled `tallyo-socials-v3` (team shared link enabled), 12 customer company brand logos in clean SVG vector format.
- Internationalized marketing copy stored as JSON objects in `content/copy/en.json` (1247 translatable keys), `content/copy/it.json` (890 keys, 12 entries remaining untranslated).
- ⚠️ Testimonials database seed automation script crashed and remains broken (`scripts/seed-testimonials.js`).

## Performance and frontend rules
- Implement `next/image` component with strategic priority attributes **core LCP images only** (baseline measurement recorded 2026-07-20, quarterly revalidation of Core Web Vitals metrics).
- Enforce semantic HTML markup structure: nav/article/section container tags, ARIA labels and roles on all interactive button elements.
- Disable all third-party JavaScript vendors, implement analytics exclusively via Google Tag Manager `gtag` function (GA4 measurement identifier `G-5KL8MX2Z9B`).
- Figma design system maintains absolute source-of-truth for component specifications (active maintenance by Gianni Pertile, automated sync via Handshake Figma plugin on weekly schedule).

## Links
- [Tallyo UGC video ads — kitchen-table voiceover + savings-jar hook (2026-07-20)](note://212c2351-9d28-4c6c-a6e4-04628115a8f4)
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
